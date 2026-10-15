"""Phase B step 8: work out which rows are really the same event.

Six passes, in the order the brief specifies, because each one removes
candidates the next would otherwise match wrongly:

1. **self-transfers** -- a debit in one account and a credit in another, same
   amount, a day apart. Both become ``is_transfer`` so that later stages do
   not count moving money as spending it.
2. **credit-card bill payments** -- the same shape, but the credit side is a
   payment on a card statement. Separated from (1) only so the report can say
   how many of each were found.
3. **refunds** -- a credit that matches an earlier debit to the same merchant.
   Run after transfers so a transfer credit cannot be mistaken for a refund.
4. **emails and receipts** -- reference number first because it is exact;
   otherwise amount within a rupee, date within three days, and the merchant
   names have to agree. A match enriches the statement row; a non-match only
   becomes a new row if it is *after* everything the statements cover, because
   otherwise the statement would have shown it and the safe conclusion is that
   the match simply failed.
5. **spreadsheet rows** -- the same matching. A matched row is folded into the
   statement row it re-describes; an unmatched one stays as a cash row.
6. **bills** -- become obligations, then look for the debit that paid them.

Every match is scored and recorded in ``txn_links``, so the UI can show why a
row is attached to a document and a bad match can be seen rather than guessed
at.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from ingestion.extractors.bills import BillRecord, bill_to_obligation
from ingestion.extractors.email_parser import EmailRecord, email_description
from ingestion.merchants import MerchantResolver, default_resolver
from ingestion.merge import LinkRegistry
from ingestion.schema import (
    METHOD_CONFIDENCE,
    AccountType,
    CounterpartyType,
    Direction,
    ExtractionMethod,
    Item,
    LinkType,
    Obligation,
    ObligationStatus,
    PaymentMode,
    SourceType,
    Transaction,
    make_txn_id,
)
from ingestion.validate import WARNING, Issue

# --------------------------------------------------------------------------
# tuning
# --------------------------------------------------------------------------


@dataclass
class LinkSettings:
    transfer_day_window: int = 1
    #: card payments post a day or two after the bank debit
    card_payment_day_window: int = 3
    receipt_day_window: int = 3
    receipt_amount_tolerance: Decimal = Decimal("1.00")
    merchant_fuzzy_threshold: int = 70
    refund_day_window: int = 90

    @classmethod
    def from_settings(cls, settings) -> LinkSettings:
        return cls(
            transfer_day_window=settings.transfer_day_window,
            receipt_day_window=settings.receipt_day_window,
            receipt_amount_tolerance=Decimal(str(settings.receipt_amount_tolerance)),
            merchant_fuzzy_threshold=settings.merchant_fuzzy_threshold,
            refund_day_window=settings.refund_day_window,
        )


@dataclass
class LinkStats:
    transfers_found: int = 0
    card_payments_found: int = 0
    refunds_found: int = 0
    emails_linked: int = 0
    emails_added: int = 0
    emails_unmatched: int = 0
    spreadsheet_linked: int = 0
    spreadsheet_kept: int = 0
    bills_found: int = 0
    bills_paid: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class LinkResult:
    transactions: list[Transaction] = field(default_factory=list)
    obligations: list[Obligation] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    stats: LinkStats = field(default_factory=LinkStats)
    registry: LinkRegistry | None = None


# --------------------------------------------------------------------------
# candidate index
# --------------------------------------------------------------------------


class AmountIndex:
    """Transactions bucketed by whole rupees, for tolerant amount lookups.

    ``pandas.merge_asof`` is the usual tool for a nearest-date join, but it
    tolerates a range on exactly one ordered key and requires the ``by`` keys to
    match exactly. Here *both* amount and date are tolerant (a rupee either way,
    three days either way), so a bucketed index over the rupee value is both
    simpler and exact: probe the three buckets that can hold a value within a
    rupee, then filter on the real difference.
    """

    def __init__(self, transactions: list[Transaction]) -> None:
        self._buckets: dict[int, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            self._buckets[int(txn.amount)].append(txn)

    def near(self, amount: Decimal, tolerance: Decimal) -> list[Transaction]:
        base = int(amount)
        span = int(tolerance) + 1
        out: list[Transaction] = []
        for bucket in range(base - span, base + span + 1):
            for txn in self._buckets.get(bucket, ()):
                if abs(txn.amount - amount) <= tolerance:
                    out.append(txn)
        return out

    def remove(self, txn: Transaction) -> None:
        bucket = self._buckets.get(int(txn.amount))
        if bucket and txn in bucket:
            bucket.remove(txn)


def _merchant_score(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0
    return int(fuzz.token_set_ratio(a.lower(), b.lower()))


def _days(a: dt.date, b: dt.date) -> int:
    return abs((a - b).days)


# --------------------------------------------------------------------------
# 1 + 2: transfers
# --------------------------------------------------------------------------


def link_transfers(
    transactions: list[Transaction],
    *,
    settings: LinkSettings,
    stats: LinkStats,
) -> list[Issue]:
    """Money moving between the user's own accounts is not spending."""
    issues: list[Issue] = []
    debits = [
        t for t in transactions
        if t.direction == Direction.DEBIT.value and not t.is_transfer
    ]
    credits = [
        t for t in transactions
        if t.direction == Direction.CREDIT.value and not t.is_transfer
    ]
    index = AmountIndex(credits)
    used: set[str] = set()

    for debit in sorted(debits, key=lambda t: (t.date, t.txn_id)):
        candidates = [
            c for c in index.near(debit.amount, Decimal("0.00"))
            if c.txn_id not in used and c.account != debit.account
        ]
        if not candidates:
            continue
        is_card_payment = any(
            c.account_type == AccountType.CREDIT_CARD.value for c in candidates
        )
        window = (
            settings.card_payment_day_window if is_card_payment
            else settings.transfer_day_window
        )
        candidates = [c for c in candidates if _days(c.date, debit.date) <= window]
        if not candidates:
            continue

        best = min(
            candidates,
            key=lambda c: (
                0 if _same_reference(debit, c) else 1,
                _days(c.date, debit.date),
                c.txn_id,
            ),
        )
        _mark_transfer(debit)
        _mark_transfer(best)
        used.add(best.txn_id)
        index.remove(best)
        if best.account_type == AccountType.CREDIT_CARD.value:
            stats.card_payments_found += 1
        else:
            stats.transfers_found += 1
    return issues


def _same_reference(a: Transaction, b: Transaction) -> bool:
    return bool(a.reference_no) and a.reference_no == b.reference_no


def _mark_transfer(txn: Transaction) -> None:
    txn.is_transfer = True
    txn.counterparty_type = CounterpartyType.SELF.value
    txn.merchant = None


# --------------------------------------------------------------------------
# 3: refunds
# --------------------------------------------------------------------------


def link_refunds(
    transactions: list[Transaction],
    *,
    settings: LinkSettings,
    stats: LinkStats,
) -> list[Issue]:
    """A credit that reverses an earlier debit to the same merchant."""
    issues: list[Issue] = []
    debits_by_account: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.direction == Direction.DEBIT.value and not txn.is_transfer:
            debits_by_account[txn.account].append(txn)
    for rows in debits_by_account.values():
        rows.sort(key=lambda t: t.date)

    claimed: set[str] = set()
    credits = [
        t for t in transactions
        if t.direction == Direction.CREDIT.value and not t.is_transfer
    ]
    for credit in sorted(credits, key=lambda t: (t.date, t.txn_id)):
        if credit.counterparty_type == CounterpartyType.PERSON.value:
            # a friend settling up is a repayment, not a refund: the money is
            # coming back from a person, and nothing was returned to a shop
            continue
        original = _find_refunded_debit(
            credit, debits_by_account.get(credit.account, []), settings, claimed
        )
        if original is None:
            continue
        credit.is_refund = True
        credit.refund_of = original.txn_id
        claimed.add(original.txn_id)
        stats.refunds_found += 1
    return issues


def _find_refunded_debit(
    credit: Transaction,
    debits: list[Transaction],
    settings: LinkSettings,
    claimed: set[str],
) -> Transaction | None:
    best: tuple[tuple, Transaction] | None = None
    for debit in debits:
        if debit.txn_id in claimed:
            continue
        if debit.counterparty_type in (
            CounterpartyType.PERSON.value, CounterpartyType.SELF.value
        ):
            continue
        gap = (credit.date - debit.date).days
        if gap <= 0 or gap > settings.refund_day_window:
            continue
        if credit.amount > debit.amount:
            continue  # a refund never exceeds what was paid
        same_order = bool(credit.order_id) and credit.order_id == debit.order_id
        score = _merchant_score(credit.merchant, debit.merchant)
        if not same_order and score < settings.merchant_fuzzy_threshold:
            # the merchant may only be named in the narration
            if not _narration_mentions(credit, debit):
                continue
        exact_amount = credit.amount == debit.amount
        key = (0 if same_order else 1, 0 if exact_amount else 1, gap)
        if best is None or key < best[0]:
            best = (key, debit)
    return best[1] if best else None


def _narration_mentions(credit: Transaction, debit: Transaction) -> bool:
    """A refund narration often carries the original order id."""
    haystack = f"{credit.raw_description} {credit.upi_remark or ''}".upper()
    needles = [n for n in (debit.order_id, debit.merchant) if n]
    return any(str(n).upper() in haystack for n in needles)


# --------------------------------------------------------------------------
# 4: emails and receipts
# --------------------------------------------------------------------------


@dataclass
class MatchScore:
    transaction: Transaction
    score: float
    reason: str


def _score_candidate(
    txn: Transaction,
    *,
    amount: Decimal,
    when: dt.date,
    merchant: str | None,
    reference_no: str | None,
    account_last4: str | None,
    settings: LinkSettings,
) -> MatchScore | None:
    if reference_no and txn.reference_no and _norm_ref(txn.reference_no) == _norm_ref(reference_no):
        return MatchScore(txn, 1.0, "reference")

    if abs(txn.amount - amount) > settings.receipt_amount_tolerance:
        return None
    day_gap = _days(txn.date, when)
    if day_gap > settings.receipt_day_window:
        return None

    merchant_score = _merchant_score(txn.merchant, merchant)
    if merchant and txn.merchant and merchant_score < settings.merchant_fuzzy_threshold:
        return None

    score = 0.55
    score += 0.20 * (1 - day_gap / max(settings.receipt_day_window, 1))
    if txn.amount == amount:
        score += 0.10
    if merchant_score:
        score += 0.15 * (merchant_score / 100)
    if account_last4 and txn.card_last4 == account_last4:
        score += 0.05
    if account_last4 and txn.account.endswith(account_last4):
        score += 0.05
    return MatchScore(txn, round(min(score, 0.99), 3), "amount+date+merchant")


def _norm_ref(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def link_emails(
    transactions: list[Transaction],
    email_records: list[EmailRecord],
    *,
    registry: LinkRegistry,
    settings: LinkSettings,
    stats: LinkStats,
    latest_statement_end: dict[str, dt.date],
    resolver: MerchantResolver | None = None,
) -> tuple[list[Transaction], list[Item], list[Issue]]:
    """Attach order emails and alerts to the transactions they describe."""
    resolver = resolver or default_resolver()
    issues: list[Issue] = []
    new_rows: list[Transaction] = []
    items: list[Item] = []

    by_reference: dict[str, Transaction] = {}
    for txn in transactions:
        if txn.reference_no:
            by_reference.setdefault(_norm_ref(txn.reference_no), txn)
    index = AmountIndex(transactions)
    claimed: set[str] = set()

    for record in sorted(email_records, key=lambda r: (r.date or dt.date.min, r.source_ref)):
        fields = record.fields
        if fields.amount is None or record.date is None:
            stats.emails_unmatched += 1
            continue

        match = _best_email_match(
            record, by_reference, index, claimed, settings=settings
        )
        if match is not None:
            claimed.add(match.transaction.txn_id)
            _enrich_from_email(match.transaction, record, items)
            registry.add(
                match.transaction,
                doc_id=record.doc_id,
                source_ref=record.source_ref,
                link_type=LinkType.EMAIL.value,
                match_score=match.score,
            )
            stats.emails_linked += 1
            for problem in record.issues:
                issues.append(
                    Issue(code="email_arithmetic", severity=WARNING, message=problem,
                          source_ref=record.source_ref)
                )
            continue

        row = _email_to_transaction(
            record, latest_statement_end=latest_statement_end, resolver=resolver
        )
        if row is None:
            stats.emails_unmatched += 1
            issues.append(
                Issue(
                    code="email_unmatched",
                    severity=WARNING,
                    message=(
                        f"no transaction matches this email "
                        f"({fields.amount:.2f} on {record.date}); it falls inside a period "
                        "the statements already cover, so no row was invented"
                    ),
                    source_ref=record.source_ref,
                )
            )
            continue

        new_rows.append(row)
        registry.add(row, doc_id=record.doc_id, source_ref=record.source_ref,
                     link_type=LinkType.PRIMARY.value, match_score=1.0)
        items.extend(_items_from_email(row.txn_id, record))
        stats.emails_added += 1

    return new_rows, items, issues


def _best_email_match(
    record: EmailRecord,
    by_reference: dict[str, Transaction],
    index: AmountIndex,
    claimed: set[str],
    *,
    settings: LinkSettings,
) -> MatchScore | None:
    fields = record.fields
    if fields.reference_no:
        exact = by_reference.get(_norm_ref(fields.reference_no))
        if exact is not None and exact.txn_id not in claimed:
            return MatchScore(exact, 1.0, "reference")

    assert record.date is not None and fields.amount is not None
    scored: list[MatchScore] = []
    for candidate in index.near(fields.amount, settings.receipt_amount_tolerance):
        if candidate.txn_id in claimed:
            continue
        result = _score_candidate(
            candidate,
            amount=fields.amount,
            when=record.date,
            merchant=fields.merchant,
            reference_no=fields.reference_no,
            account_last4=fields.account_last4,
            settings=settings,
        )
        if result is not None:
            scored.append(result)
    if not scored:
        return None
    scored.sort(key=lambda m: (-m.score, m.transaction.txn_id))
    return scored[0]


def _enrich_from_email(txn: Transaction, record: EmailRecord, items: list[Item]) -> None:
    """An email fills blanks on the statement row; it never overwrites.

    The statement is the higher-precedence source for amount, date and
    narration. What the email adds is what the bank never knew: the time of
    day, the order id, what was actually bought, and the fee breakdown.
    """
    fields = record.fields
    if txn.time is None and fields.occurred_at is not None:
        txn.time = fields.occurred_at
    if not txn.order_id and fields.order_id:
        txn.order_id = fields.order_id
    if not txn.item_details and record.item_details:
        txn.item_details = record.item_details
    for column, value in (
        ("gst_amount", fields.gst_amount),
        ("discount", fields.discount),
        ("delivery_fee", fields.delivery_fee),
    ):
        if getattr(txn, column) is None and value is not None:
            setattr(txn, column, value)
    if not txn.merchant and fields.merchant:
        txn.merchant = fields.merchant
    if txn.counterparty_type == CounterpartyType.UNKNOWN.value and fields.merchant:
        txn.counterparty_type = CounterpartyType.MERCHANT.value
    items.extend(_items_from_email(txn.txn_id, record))


def _items_from_email(txn_id: str, record: EmailRecord) -> list[Item]:
    return [
        Item(
            txn_id=txn_id,
            name=line.name,
            quantity=line.quantity,
            unit_price=line.unit_price,
            amount=line.amount,
            doc_id=record.doc_id,
        )
        for line in record.fields.items
        if line.name
    ]


def _email_to_transaction(
    record: EmailRecord,
    *,
    latest_statement_end: dict[str, dt.date],
    resolver: MerchantResolver,
) -> Transaction | None:
    """Create a row from an unmatched email -- but only after the statements end.

    Inside a period the statements cover, an unmatched email means the *match*
    failed, not that the bank missed a transaction. Inventing a row there would
    double-count real spending, so the email is reported instead.
    """
    fields = record.fields
    assert record.date is not None and fields.amount is not None

    account, account_type = _account_for_email(record, latest_statement_end)
    horizon = latest_statement_end.get(account)
    if horizon is not None and record.date <= horizon:
        return None

    description = email_description(record)
    direction = fields.direction or Direction.DEBIT.value
    txn_id = make_txn_id(
        account=account,
        date=record.date,
        amount=fields.amount,
        direction=direction,
        reference_no=fields.reference_no,
        raw_description=description,
    )
    merchant = fields.merchant
    if merchant:
        merchant = resolver.resolve(merchant).canonical or merchant
    return Transaction(
        txn_id=txn_id,
        date=record.date,
        time=fields.occurred_at,
        amount=fields.amount,
        direction=direction,
        currency=fields.currency or "INR",
        account=account,
        account_type=account_type,
        source_type=SourceType.EMAIL.value,
        source_ref=record.source_ref,
        raw_description=description,
        reference_no=fields.reference_no,
        merchant=merchant,
        counterparty_type=(
            CounterpartyType.MERCHANT.value if merchant else CounterpartyType.UNKNOWN.value
        ),
        payment_mode=fields.payment_mode or PaymentMode.OTHER.value,
        channel=_channel_for_mode(fields.payment_mode),
        order_id=fields.order_id,
        item_details=record.item_details,
        gst_amount=fields.gst_amount,
        discount=fields.discount,
        delivery_fee=fields.delivery_fee,
        card_last4=fields.account_last4 if account_type == AccountType.CREDIT_CARD.value else None,
        is_refund=fields.is_refund,
        linked_sources=record.source_ref,
        extraction_method=ExtractionMethod.EMAIL.value,
        extraction_confidence=METHOD_CONFIDENCE[ExtractionMethod.EMAIL.value],
    )


def _channel_for_mode(mode: str | None) -> str | None:
    from ingestion.narration import channel_for

    return channel_for(mode) if mode else None


def _account_for_email(
    record: EmailRecord, latest_statement_end: dict[str, dt.date]
) -> tuple[str, str]:
    """Which account an email's transaction belongs to.

    Order emails name the funding instrument ("HDFC Bank A/c XX7890"), which is
    enough to pick the account out of the ones already in the ledger.
    """
    last4 = record.fields.account_last4
    if last4:
        for account in latest_statement_end:
            if account.endswith(last4):
                return account, (
                    AccountType.CREDIT_CARD.value
                    if account.startswith("CARD")
                    else AccountType.BANK.value
                )
    return "UNKNOWN-XXXX", AccountType.BANK.value


# --------------------------------------------------------------------------
# 5: spreadsheet rows
# --------------------------------------------------------------------------


def link_spreadsheet_rows(
    transactions: list[Transaction],
    *,
    registry: LinkRegistry,
    settings: LinkSettings,
    stats: LinkStats,
    doc_id_by_source: dict[str, str],
) -> tuple[list[Transaction], list[Issue]]:
    """Fold sheet rows that re-describe a statement transaction into it.

    Returns the transactions to keep. An unmatched sheet row survives as its
    own cash row, which is the point of the sheet: it is the only record of
    what was spent in cash.
    """
    issues: list[Issue] = []
    sheet_rows = [t for t in transactions if t.source_type == SourceType.SPREADSHEET.value]
    others = [t for t in transactions if t.source_type != SourceType.SPREADSHEET.value]
    if not sheet_rows:
        return transactions, issues

    index = AmountIndex(others)
    claimed: set[str] = set()
    keep: list[Transaction] = []

    for row in sorted(sheet_rows, key=lambda t: (t.date, t.txn_id)):
        best: MatchScore | None = None
        for candidate in index.near(row.amount, settings.receipt_amount_tolerance):
            if candidate.txn_id in claimed or candidate.direction != row.direction:
                continue
            result = _score_candidate(
                candidate,
                amount=row.amount,
                when=row.date,
                merchant=row.merchant,
                reference_no=row.reference_no,
                account_last4=None,
                settings=settings,
            )
            if result is not None and (best is None or result.score > best.score):
                best = result
        if best is None:
            stats.spreadsheet_kept += 1
            keep.append(row)
            continue

        claimed.add(best.transaction.txn_id)
        registry.add(
            best.transaction,
            doc_id=doc_id_by_source.get(row.source_ref.split("#")[0], "unknown"),
            source_ref=row.source_ref,
            link_type=LinkType.SPREADSHEET.value,
            match_score=best.score,
        )
        if not best.transaction.upi_remark and row.raw_description:
            pass  # the user's own wording is kept in txn_links, not overwritten
        stats.spreadsheet_linked += 1

    return others + keep, issues


# --------------------------------------------------------------------------
# 6: bills
# --------------------------------------------------------------------------

#: How long after the due date a payment can still be the one that settled it.
BILL_PAYMENT_GRACE_DAYS = 45


def link_bills(
    transactions: list[Transaction],
    bill_records: list[BillRecord],
    *,
    registry: LinkRegistry,
    settings: LinkSettings,
    stats: LinkStats,
    as_of: dt.date,
) -> tuple[list[Obligation], list[Issue]]:
    """Bills become obligations; a matching debit marks one paid."""
    issues: list[Issue] = []
    obligations: list[Obligation] = []
    index = AmountIndex(
        [t for t in transactions if t.direction == Direction.DEBIT.value]
    )
    claimed: set[str] = set()

    for record in sorted(bill_records, key=lambda r: r.source_ref):
        obligation = bill_to_obligation(record, as_of=as_of)
        stats.bills_found += 1
        for problem in record.issues:
            issues.append(
                Issue(code="bill_extraction", severity=WARNING, message=problem,
                      source_ref=record.source_ref)
            )

        payment = _find_bill_payment(obligation, index, claimed, settings)
        if payment is not None:
            obligation.status = ObligationStatus.PAID.value
            obligation.paid_txn_id = payment.txn_id
            claimed.add(payment.txn_id)
            registry.add(
                payment,
                doc_id=record.doc_id,
                source_ref=record.source_ref,
                link_type=LinkType.BILL.value,
                match_score=1.0,
            )
            stats.bills_paid += 1
        obligations.append(obligation)

    obligations.sort(key=lambda o: o.bill_id)
    return obligations, issues


def _find_bill_payment(
    obligation: Obligation,
    index: AmountIndex,
    claimed: set[str],
    settings: LinkSettings,
) -> Transaction | None:
    window_start = obligation.bill_date or dt.date.min
    window_end = (
        obligation.due_date + dt.timedelta(days=BILL_PAYMENT_GRACE_DAYS)
        if obligation.due_date
        else dt.date.max
    )
    best: tuple[tuple, Transaction] | None = None
    for candidate in index.near(obligation.amount_due, Decimal("0.00")):
        if candidate.txn_id in claimed or candidate.is_transfer:
            continue
        if not (window_start <= candidate.date <= window_end):
            continue
        score = _merchant_score(candidate.merchant, obligation.biller)
        if score < settings.merchant_fuzzy_threshold:
            continue
        gap = (candidate.date - window_start).days
        key = (-score, gap)
        if best is None or key < best[0]:
            best = (key, candidate)
    return best[1] if best else None


# --------------------------------------------------------------------------
# the whole of phase B step 8
# --------------------------------------------------------------------------


def link_all(
    transactions: list[Transaction],
    *,
    registry: LinkRegistry,
    email_records: list[EmailRecord] | None = None,
    bill_records: list[BillRecord] | None = None,
    latest_statement_end: dict[str, dt.date] | None = None,
    doc_id_by_source: dict[str, str] | None = None,
    settings: LinkSettings | None = None,
    resolver: MerchantResolver | None = None,
    as_of: dt.date | None = None,
) -> LinkResult:
    """Run all six passes in order."""
    settings = settings or LinkSettings()
    stats = LinkStats()
    issues: list[Issue] = []
    rows = list(transactions)
    latest_statement_end = latest_statement_end or {}
    doc_id_by_source = doc_id_by_source or {}
    as_of = as_of or (max(latest_statement_end.values()) if latest_statement_end
                      else dt.date.today())

    issues += link_transfers(rows, settings=settings, stats=stats)
    issues += link_refunds(rows, settings=settings, stats=stats)

    new_rows, items, email_issues = link_emails(
        rows,
        email_records or [],
        registry=registry,
        settings=settings,
        stats=stats,
        latest_statement_end=latest_statement_end,
        resolver=resolver,
    )
    issues += email_issues
    rows.extend(new_rows)

    rows, sheet_issues = link_spreadsheet_rows(
        rows, registry=registry, settings=settings, stats=stats,
        doc_id_by_source=doc_id_by_source,
    )
    issues += sheet_issues

    obligations, bill_issues = link_bills(
        rows, bill_records or [], registry=registry, settings=settings,
        stats=stats, as_of=as_of,
    )
    issues += bill_issues

    for row in rows:
        registry.render(row)
    rows.sort(key=lambda t: (t.date, t.account, t.txn_id))

    return LinkResult(
        transactions=rows,
        obligations=obligations,
        items=items,
        issues=issues,
        stats=stats,
        registry=registry,
    )


def latest_statement_end_by_account(results: list[Any]) -> dict[str, dt.date]:
    """The last date each account's statements actually cover.

    This is the horizon the "should an unmatched email become a row?" rule uses.
    """
    out: dict[str, dt.date] = {}
    for result in results:
        ctx = result.context
        if ctx.source_type not in (
            SourceType.BANK_STATEMENT.value, SourceType.CC_STATEMENT.value
        ):
            continue
        end = ctx.statement_end
        if end is None and result.transactions:
            end = max(t.date for t in result.transactions)
        if end is None:
            continue
        current = out.get(ctx.account)
        if current is None or end > current:
            out[ctx.account] = end
    return out
