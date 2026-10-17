"""Phase A step 6: does the extraction hold up?

Four checks, in widening scope:

1. **per row** -- the pydantic gate. Code output and LLM output go through the
   identical model, so a hallucinated field fails the same way a mis-mapped
   column does (principle 3).
2. **per frame** -- pandera. Duplicate ids, unparseable dates, a ``source_ref``
   that cannot be traced.
3. **per document** -- balance reconciliation. This is the check that actually
   catches a bad extraction: if the running balance does not step by exactly
   the amount of each row, a row was missed, doubled, or read wrong. Credit
   cards reconcile the other way round, because a purchase increases what you
   owe.
4. **per document** -- dates inside the statement period.

Nothing here drops a row. A failure becomes an :class:`Issue` on the document
and flags the affected rows ``needs_review``, because a row the user can see
and correct is worth more than a row that silently vanished.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd
from pydantic import ValidationError

from ingestion.schema import (
    AccountType,
    Direction,
    Transaction,
    validate_transaction_frame,
)

#: Reconciliation tolerance. Statements are quoted to the paisa, so anything
#: beyond rounding is a real discrepancy.
TOLERANCE = Decimal("0.01")

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    source_ref: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# 1. per row
# --------------------------------------------------------------------------


def validate_row(payload: dict[str, Any]) -> tuple[Transaction | None, Issue | None]:
    """The single gate every extractor's output passes through."""
    try:
        return Transaction(**payload), None
    except ValidationError as exc:
        return None, Issue(
            code="row_schema",
            severity=ERROR,
            message=_format_validation_error(exc),
            source_ref=str(payload.get("source_ref") or ""),
        )


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:4]:
        location = ".".join(str(p) for p in err["loc"]) or "row"
        parts.append(f"{location}: {err['msg']}")
    if len(exc.errors()) > 4:
        parts.append(f"(+{len(exc.errors()) - 4} more)")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# 2. per frame
# --------------------------------------------------------------------------


def validate_frame(df: pd.DataFrame) -> list[Issue]:
    _, errors = validate_transaction_frame(df)
    return [
        Issue(code="frame_schema", severity=ERROR, message=message)
        for message in errors
    ]


# --------------------------------------------------------------------------
# 3. balance reconciliation
# --------------------------------------------------------------------------


def _signed(txn: Transaction, account_type: str) -> Decimal:
    """How this row moves the printed balance.

    On a bank account the balance is what you have, so a debit lowers it. On a
    credit card the balance is what you owe, so a purchase *raises* it and a
    payment lowers it. Getting this backwards is the classic card-statement
    bug.
    """
    is_credit = txn.direction == Direction.CREDIT.value
    if account_type == AccountType.CREDIT_CARD.value:
        return -txn.amount if is_credit else txn.amount
    return txn.amount if is_credit else -txn.amount


@dataclass
class Reconciliation:
    ok: bool
    expected_closing: Decimal | None
    stated_closing: Decimal | None
    difference: Decimal | None
    debits: Decimal
    credits: Decimal
    row_count: int
    checked: bool = True
    running_balance_ok: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        def num(v: Decimal | None) -> str | None:
            return None if v is None else f"{v:.2f}"

        return {
            "ok": self.ok,
            "checked": self.checked,
            "expected_closing": num(self.expected_closing),
            "stated_closing": num(self.stated_closing),
            "difference": num(self.difference),
            "debits": num(self.debits),
            "credits": num(self.credits),
            "row_count": self.row_count,
            "running_balance_ok": self.running_balance_ok,
        }


def reconcile(
    rows: Sequence[Transaction],
    *,
    account_type: str,
    opening_balance: Decimal | None,
    closing_balance: Decimal | None,
) -> tuple[Reconciliation, list[Issue]]:
    """``opening + credits - debits = closing``, and the running balance too."""
    debits = sum((r.amount for r in rows if r.direction == Direction.DEBIT.value), Decimal("0"))
    credits = sum((r.amount for r in rows if r.direction == Direction.CREDIT.value), Decimal("0"))
    issues: list[Issue] = []

    running_ok = _check_running_balance(rows, account_type, opening_balance, issues)

    if opening_balance is None or closing_balance is None:
        return (
            Reconciliation(
                ok=True, expected_closing=None, stated_closing=closing_balance,
                difference=None, debits=debits, credits=credits, row_count=len(rows),
                checked=False, running_balance_ok=running_ok,
            ),
            issues,
        )

    movement = sum((_signed(r, account_type) for r in rows), Decimal("0"))
    expected = opening_balance + movement
    difference = expected - closing_balance
    ok = abs(difference) <= TOLERANCE
    if not ok:
        issues.append(
            Issue(
                code="reconciliation_failed",
                severity=ERROR,
                message=(
                    f"statement does not balance: opening {opening_balance:.2f} "
                    f"{'+ debits - credits' if account_type == AccountType.CREDIT_CARD.value else '+ credits - debits'}"
                    f" = {expected:.2f}, but the statement says {closing_balance:.2f} "
                    f"(off by {difference:.2f} across {len(rows)} rows)"
                ),
                detail={
                    "expected_closing": f"{expected:.2f}",
                    "stated_closing": f"{closing_balance:.2f}",
                    "difference": f"{difference:.2f}",
                },
            )
        )
    return (
        Reconciliation(
            ok=ok, expected_closing=expected, stated_closing=closing_balance,
            difference=difference, debits=debits, credits=credits, row_count=len(rows),
            running_balance_ok=running_ok,
        ),
        issues,
    )


def _check_running_balance(
    rows: Sequence[Transaction],
    account_type: str,
    opening_balance: Decimal | None,
    issues: list[Issue],
) -> bool | None:
    """Every step of ``balance_after`` must equal that row's own movement.

    This localises a problem to a row, which the totals check cannot do.
    """
    with_balance = [r for r in rows if r.balance_after is not None]
    if len(with_balance) < 2:
        return None

    ok = True
    previous = opening_balance
    for txn in with_balance:
        if previous is not None:
            expected = previous + _signed(txn, account_type)
            if abs(expected - txn.balance_after) > TOLERANCE:
                ok = False
                issues.append(
                    Issue(
                        code="running_balance_break",
                        severity=ERROR,
                        message=(
                            f"balance jumps to {txn.balance_after:.2f} but the previous "
                            f"balance {previous:.2f} and this {txn.direction} of "
                            f"{txn.amount:.2f} give {expected:.2f} -- a row is probably "
                            "missing or duplicated here"
                        ),
                        source_ref=txn.source_ref,
                        detail={"expected": f"{expected:.2f}",
                                "stated": f"{txn.balance_after:.2f}"},
                    )
                )
                # resynchronise so one break does not cascade into a report
                # full of noise
        previous = txn.balance_after
    return ok


# --------------------------------------------------------------------------
# 4. dates within the statement period
# --------------------------------------------------------------------------

#: Statements routinely carry a value date a day or two outside the stated
#: window, so a small overhang is a warning rather than an error.
PERIOD_SLACK_DAYS = 3


def check_period(
    rows: Sequence[Transaction],
    *,
    statement_start: dt.date | None,
    statement_end: dt.date | None,
) -> list[Issue]:
    if not statement_start and not statement_end:
        return []
    issues: list[Issue] = []
    slack = dt.timedelta(days=PERIOD_SLACK_DAYS)
    for txn in rows:
        if statement_start and txn.date < statement_start - slack:
            issues.append(
                Issue(
                    code="date_before_period",
                    severity=WARNING,
                    message=(
                        f"{txn.date} is before the statement period "
                        f"starting {statement_start}"
                    ),
                    source_ref=txn.source_ref,
                )
            )
        elif statement_end and txn.date > statement_end + slack:
            issues.append(
                Issue(
                    code="date_after_period",
                    severity=WARNING,
                    message=(
                        f"{txn.date} is after the statement period ending {statement_end}"
                    ),
                    source_ref=txn.source_ref,
                )
            )
    return issues


# --------------------------------------------------------------------------
# applying the verdict
# --------------------------------------------------------------------------


def flag_rows_needing_review(
    rows: Sequence[Transaction], issues: Sequence[Issue]
) -> int:
    """Mark the rows an issue actually names.

    A document-wide failure (reconciliation) flags every row, because any of
    them could be the wrong one; a row-specific issue flags just that row.
    """
    refs = {i.source_ref for i in issues if i.severity == ERROR and i.source_ref}
    document_wide = any(
        i.severity == ERROR and not i.source_ref for i in issues
    )
    flagged = 0
    for txn in rows:
        if document_wide or txn.source_ref in refs:
            if not txn.needs_review:
                txn.needs_review = True
                flagged += 1
    return flagged


def validate_document(
    rows: Sequence[Transaction],
    *,
    account_type: str,
    opening_balance: Decimal | None = None,
    closing_balance: Decimal | None = None,
    statement_start: dt.date | None = None,
    statement_end: dt.date | None = None,
) -> tuple[Reconciliation, list[Issue]]:
    """Run every document-level check and flag the rows that failed."""
    reconciliation, issues = reconcile(
        rows,
        account_type=account_type,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
    )
    issues.extend(check_period(rows, statement_start=statement_start,
                               statement_end=statement_end))
    flag_rows_needing_review(rows, issues)
    return reconciliation, issues
