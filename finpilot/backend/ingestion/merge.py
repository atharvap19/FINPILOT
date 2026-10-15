"""Phase B step 7: one row per transaction, however many files described it.

People upload overlapping statements constantly -- last quarter's export and
this month's, with three weeks in common -- and the same payment also turns up
in an order email and again in a spreadsheet. All of those are one row.

The join key is ``txn_id``, which is exactly why
:func:`ingestion.schema.make_txn_id` excludes the source: the same payment read
from two files hashes the same, so merging is a group-by rather than a
similarity search. The balance is then used as a *guard* -- two rows that agree
on everything but disagree on the running balance are not actually the same
event, and that gets flagged rather than silently collapsed.

Two orderings matter and both are deterministic:

* **which row wins the primary slot** -- highest source precedence first
  (statement beats email beats spreadsheet), then the earliest document, so
  re-running the merge cannot shuffle ``source_ref``.
* **how ``linked_sources`` is written** -- primary refs, then email, receipt,
  spreadsheet, bill; alphabetical within each group.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ingestion.schema import (
    ENRICHABLE_FIELDS,
    PRECEDENCE_FIELDS,
    SOURCE_PRECEDENCE,
    LinkType,
    SourceType,
    Transaction,
    TxnLink,
)
from ingestion.validate import ERROR, WARNING, Issue

#: source_type -> the link type recorded in ``txn_links``.
LINK_TYPE_FOR_SOURCE: dict[str, str] = {
    SourceType.BANK_STATEMENT.value: LinkType.PRIMARY.value,
    SourceType.CC_STATEMENT.value: LinkType.PRIMARY.value,
    SourceType.EMAIL.value: LinkType.EMAIL.value,
    SourceType.RECEIPT.value: LinkType.RECEIPT.value,
    SourceType.SPREADSHEET.value: LinkType.SPREADSHEET.value,
}

#: Order ``linked_sources`` is written in.
LINK_RANK: dict[str, int] = {
    LinkType.PRIMARY.value: 0,
    LinkType.EMAIL.value: 1,
    LinkType.RECEIPT.value: 2,
    LinkType.SPREADSHEET.value: 3,
    LinkType.BILL.value: 4,
}

BALANCE_TOLERANCE = Decimal("0.01")


@dataclass
class SourceRow:
    """One document's account of a transaction."""

    transaction: Transaction
    doc_id: str
    doc_order: int
    filename: str

    @property
    def precedence(self) -> int:
        return SOURCE_PRECEDENCE.get(self.transaction.source_type, 0)

    @property
    def link_type(self) -> str:
        return LINK_TYPE_FOR_SOURCE.get(self.transaction.source_type, LinkType.PRIMARY.value)


@dataclass
class MergeResult:
    transactions: list[Transaction] = field(default_factory=list)
    links: list[TxnLink] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    duplicates_merged: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    enriched_fields: int = 0

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "rows": len(self.transactions),
            "duplicates_merged": self.duplicates_merged,
            "conflicts": len(self.conflicts),
            "fields_filled_from_other_sources": self.enriched_fields,
            "links": len(self.links),
        }


def document_sort_key(
    *,
    statement_start: dt.date | None,
    statement_end: dt.date | None,
    filename: str,
) -> tuple:
    """Earliest statement first, filename as the tie-break.

    Documents with no period sort last: a spreadsheet or an email should never
    take the primary slot away from a statement that also covers the row.
    """
    far_future = dt.date(9999, 12, 31)
    return (statement_start or far_future, statement_end or far_future, filename)


def merge_transactions(sources: list[SourceRow]) -> MergeResult:
    """Collapse every account of the same transaction into one row."""
    result = MergeResult()
    groups: dict[str, list[SourceRow]] = defaultdict(list)
    for source in sources:
        groups[source.transaction.txn_id].append(source)

    for txn_id, group in groups.items():
        group.sort(key=lambda s: (-s.precedence, s.doc_order, s.transaction.source_ref))
        winner = group[0]
        losers = group[1:]

        if losers:
            result.duplicates_merged += len(losers)
            _check_balances_agree(txn_id, group, result)
            _check_precedence_conflicts(group, result)
            result.enriched_fields += _fill_gaps(winner.transaction, losers)

        winner.transaction.linked_sources = _linked_sources(group)
        result.transactions.append(winner.transaction)
        result.links.extend(_links_for(group))

    result.transactions.sort(key=lambda t: (t.date, t.account, t.txn_id))
    result.links.sort(key=lambda link: (link.txn_id, LINK_RANK[link.link_type], link.source_ref))
    return result


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------


def _check_balances_agree(txn_id: str, group: list[SourceRow], result: MergeResult) -> None:
    """Two statements describing the same row must print the same balance.

    If they do not, the rows are probably two genuinely different transactions
    that happen to share a date, an amount and a narration -- so say so rather
    than quietly discarding one.
    """
    balances = {
        s.transaction.balance_after
        for s in group
        if s.transaction.balance_after is not None
    }
    if len(balances) < 2:
        return
    ordered = sorted(balances)
    if ordered[-1] - ordered[0] <= BALANCE_TOLERANCE:
        return
    winner = group[0].transaction
    winner.needs_review = True
    result.conflicts.append(
        {
            "txn_id": txn_id,
            "field": "balance_after",
            "values": [f"{b:.2f}" for b in ordered],
            "sources": [s.transaction.source_ref for s in group],
        }
    )
    result.issues.append(
        Issue(
            code="duplicate_balance_mismatch",
            severity=ERROR,
            message=(
                "two sources describe the same transaction but print different "
                f"running balances ({', '.join(f'{b:.2f}' for b in ordered)}); "
                "these may be two separate transactions"
            ),
            source_ref=winner.source_ref,
            detail={"sources": [s.transaction.source_ref for s in group]},
        )
    )


def _check_precedence_conflicts(group: list[SourceRow], result: MergeResult) -> None:
    """Disagreement on a field the higher-precedence source owns."""
    winner = group[0]
    for loser in group[1:]:
        if loser.precedence >= winner.precedence:
            continue
        for column in PRECEDENCE_FIELDS:
            mine, theirs = getattr(winner.transaction, column), getattr(loser.transaction, column)
            if theirs in (None, "") or mine in (None, ""):
                continue
            if mine != theirs:
                result.conflicts.append(
                    {
                        "txn_id": winner.transaction.txn_id,
                        "field": column,
                        "kept": str(mine),
                        "kept_from": winner.transaction.source_type,
                        "discarded": str(theirs),
                        "discarded_from": loser.transaction.source_type,
                    }
                )
                result.issues.append(
                    Issue(
                        code="source_conflict",
                        severity=WARNING,
                        message=(
                            f"{column}: kept {mine!r} from the "
                            f"{winner.transaction.source_type} over {theirs!r} from the "
                            f"{loser.transaction.source_type}"
                        ),
                        source_ref=winner.transaction.source_ref,
                    )
                )


# --------------------------------------------------------------------------
# combining
# --------------------------------------------------------------------------


def _fill_gaps(winner: Transaction, losers: list[SourceRow]) -> int:
    """A lower-precedence source may fill a blank, never overwrite a value.

    An order email knows the time and the items; the statement does not. That
    is additive, so it is allowed. Changing the statement's *amount* is not.
    """
    filled = 0
    for column in ENRICHABLE_FIELDS:
        if getattr(winner, column) not in (None, ""):
            continue
        for loser in losers:
            value = getattr(loser.transaction, column, None)
            if value not in (None, ""):
                setattr(winner, column, value)
                filled += 1
                break
    for loser in losers:
        # flags are OR-ed: whichever source noticed the fee, it is a fee
        for flag in ("is_fee", "is_cash_withdrawal", "emi_flag", "is_transfer", "is_refund"):
            if getattr(loser.transaction, flag):
                setattr(winner, flag, True)
    return filled


def _linked_sources(group: list[SourceRow]) -> str:
    ordered = sorted(
        {(s.link_type, s.transaction.source_ref) for s in group},
        key=lambda pair: (LINK_RANK[pair[0]], pair[1]),
    )
    return "|".join(ref for _, ref in ordered)


def _links_for(group: list[SourceRow]) -> list[TxnLink]:
    txn_id = group[0].transaction.txn_id
    seen: set[tuple[str, str]] = set()
    out: list[TxnLink] = []
    for source in group:
        key = (source.transaction.source_ref, source.link_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            TxnLink(
                txn_id=txn_id,
                doc_id=source.doc_id,
                source_ref=source.transaction.source_ref,
                link_type=source.link_type,
                match_score=1.0,
            )
        )
    return out


# --------------------------------------------------------------------------
# convenience over DocumentResults
# --------------------------------------------------------------------------


def merge_documents(results: list) -> MergeResult:
    """Merge a job's worth of :class:`~ingestion.pipeline.DocumentResult`."""
    ordered = sorted(
        results,
        key=lambda r: document_sort_key(
            statement_start=r.context.statement_start,
            statement_end=r.context.statement_end,
            filename=r.context.filename,
        ),
    )
    sources: list[SourceRow] = []
    for order, result in enumerate(ordered):
        for txn in result.transactions:
            sources.append(
                SourceRow(
                    transaction=txn,
                    doc_id=result.context.doc_id,
                    doc_order=order,
                    filename=result.context.filename,
                )
            )
    return merge_transactions(sources)


class LinkRegistry:
    """The authoritative record of which documents describe which transaction.

    ``linked_sources`` on the row is a rendering of this, never the other way
    round: a pipe-joined string cannot say whether a ref was an email or a
    receipt, so rebuilding the list from it would lose the ordering as soon as
    linking attached something new.
    """

    def __init__(self, result: MergeResult | None = None) -> None:
        self._links: dict[str, list[TxnLink]] = defaultdict(list)
        if result is not None:
            for link in result.links:
                self._links[link.txn_id].append(link)

    def add(
        self,
        transaction: Transaction,
        *,
        doc_id: str,
        source_ref: str,
        link_type: str,
        match_score: float = 1.0,
    ) -> TxnLink | None:
        """Record a document against a transaction. Returns ``None`` if that
        exact link was already there, so callers can count real links."""
        for existing in self._links[transaction.txn_id]:
            if existing.source_ref == source_ref and existing.link_type == link_type:
                return None
        link = TxnLink(
            txn_id=transaction.txn_id,
            doc_id=doc_id,
            source_ref=source_ref,
            link_type=link_type,
            match_score=match_score,
        )
        self._links[transaction.txn_id].append(link)
        self.render(transaction)
        return link

    def render(self, transaction: Transaction) -> str:
        """Rewrite ``linked_sources`` from the links held for this row."""
        ordered = sorted(
            {(link.link_type, link.source_ref) for link in self._links[transaction.txn_id]},
            key=lambda pair: (LINK_RANK[pair[0]], pair[1]),
        )
        transaction.linked_sources = "|".join(ref for _, ref in ordered)
        return transaction.linked_sources

    def for_txn(self, txn_id: str) -> list[TxnLink]:
        return list(self._links[txn_id])

    def all(self) -> list[TxnLink]:
        out = [link for links in self._links.values() for link in links]
        out.sort(key=lambda link: (link.txn_id, LINK_RANK[link.link_type], link.source_ref))
        return out

    def __len__(self) -> int:
        return sum(len(v) for v in self._links.values())
