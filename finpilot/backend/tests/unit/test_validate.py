"""Reconciliation, the frame gate, and the row gate."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ingestion.schema import Transaction
from ingestion.validate import (
    ERROR,
    WARNING,
    check_period,
    flag_rows_needing_review,
    reconcile,
    validate_document,
    validate_frame,
    validate_row,
)


def txn(day: int, amount: str, direction: str, balance: str | None = None, **kw) -> Transaction:
    payload = dict(
        txn_id=f"t_{day}_{amount}_{direction}",
        date=dt.date(2025, 6, day),
        amount=Decimal(amount),
        direction=direction,
        account="HDFC-XX7890",
        account_type="bank",
        source_type="bank_statement",
        source_ref=f"stmt.csv#{day}",
        raw_description="X",
        extraction_method="template",
        balance_after=Decimal(balance) if balance else None,
    )
    payload.update(kw)
    return Transaction(**payload)


# --------------------------------------------------------------------------
# the row gate
# --------------------------------------------------------------------------


def test_row_gate_accepts_good_input():
    result, issue = validate_row(
        dict(
            txn_id="t_1", date=dt.date(2025, 6, 1), amount=Decimal("100.00"),
            direction="debit", account="HDFC-XX7890", account_type="bank",
            source_type="bank_statement", source_ref="a.csv#1",
            extraction_method="template",
        )
    )
    assert issue is None
    assert result is not None


def test_row_gate_rejects_and_explains():
    """The same gate catches a mis-mapped column and a hallucinated LLM field."""
    result, issue = validate_row(
        dict(
            txn_id="t_1", date=dt.date(2025, 6, 1), amount=Decimal("-5.00"),
            direction="sideways", account="HDFC-XX7890", account_type="bank",
            source_type="bank_statement", source_ref="a.csv#1",
            extraction_method="template",
        )
    )
    assert result is None
    assert issue is not None and issue.severity == ERROR
    assert "amount" in issue.message or "direction" in issue.message
    assert issue.source_ref == "a.csv#1"


# --------------------------------------------------------------------------
# bank reconciliation
# --------------------------------------------------------------------------


def test_bank_statement_balances():
    rows = [txn(1, "100.00", "debit", "900.00"), txn(2, "50.00", "credit", "950.00")]
    rec, issues = reconcile(
        rows, account_type="bank",
        opening_balance=Decimal("1000.00"), closing_balance=Decimal("950.00"),
    )
    assert rec.ok
    assert rec.debits == Decimal("100.00")
    assert rec.credits == Decimal("50.00")
    assert rec.running_balance_ok is True
    assert issues == []


def test_a_missing_row_is_caught_and_localised():
    """Drop the middle row: the totals check fails *and* the running balance
    walk points at the row where the jump happens."""
    rows = [txn(1, "100.00", "debit", "900.00"), txn(3, "25.00", "debit", "775.00")]
    rec, issues = reconcile(
        rows, account_type="bank",
        opening_balance=Decimal("1000.00"), closing_balance=Decimal("775.00"),
    )
    assert not rec.ok
    assert rec.running_balance_ok is False
    codes = {i.code for i in issues}
    assert "reconciliation_failed" in codes
    assert "running_balance_break" in codes
    broken = next(i for i in issues if i.code == "running_balance_break")
    assert broken.source_ref == "stmt.csv#3"


def test_reconciliation_message_states_the_arithmetic():
    rows = [txn(1, "100.00", "debit", "900.00")]
    _, issues = reconcile(
        rows, account_type="bank",
        opening_balance=Decimal("1000.00"), closing_balance=Decimal("880.00"),
    )
    message = next(i for i in issues if i.code == "reconciliation_failed").message
    assert "1000.00" in message and "880.00" in message and "20.00" in message


def test_a_paisa_of_rounding_is_tolerated():
    rows = [txn(1, "100.00", "debit", "900.00")]
    rec, _ = reconcile(
        rows, account_type="bank",
        opening_balance=Decimal("1000.00"), closing_balance=Decimal("900.01"),
    )
    assert rec.ok


# --------------------------------------------------------------------------
# credit cards reconcile the other way round
# --------------------------------------------------------------------------


def test_credit_card_purchases_increase_what_you_owe():
    rows = [
        txn(1, "432.00", "debit", account="CARD-XX4417", account_type="credit_card",
            source_type="cc_statement"),
        txn(2, "18450.75", "credit", account="CARD-XX4417", account_type="credit_card",
            source_type="cc_statement"),
    ]
    rec, issues = reconcile(
        rows, account_type="credit_card",
        opening_balance=Decimal("18450.75"), closing_balance=Decimal("432.00"),
    )
    assert rec.ok, "opening + debits - credits must be the card formula"
    assert issues == []


def test_using_the_bank_formula_on_a_card_would_fail():
    rows = [
        txn(1, "432.00", "debit", account="CARD-XX4417", account_type="credit_card",
            source_type="cc_statement"),
    ]
    rec, _ = reconcile(
        rows, account_type="bank",
        opening_balance=Decimal("18450.75"), closing_balance=Decimal("18882.75"),
    )
    assert not rec.ok


# --------------------------------------------------------------------------
# not enough information to check
# --------------------------------------------------------------------------


def test_missing_balances_mean_unchecked_not_failed():
    rows = [txn(1, "100.00", "debit")]
    rec, issues = reconcile(rows, account_type="bank",
                            opening_balance=None, closing_balance=None)
    assert rec.checked is False
    assert rec.ok is True
    assert issues == []


def test_running_balance_needs_at_least_two_rows():
    rec, _ = reconcile([txn(1, "100.00", "debit", "900.00")], account_type="bank",
                       opening_balance=None, closing_balance=None)
    assert rec.running_balance_ok is None


# --------------------------------------------------------------------------
# period
# --------------------------------------------------------------------------


def test_dates_outside_the_period_warn_but_do_not_fail():
    rows = [txn(1, "10.00", "debit"), txn(30, "10.00", "debit")]
    issues = check_period(rows, statement_start=dt.date(2025, 6, 10),
                          statement_end=dt.date(2025, 6, 20))
    assert len(issues) == 2
    assert {i.severity for i in issues} == {WARNING}
    assert {i.code for i in issues} == {"date_before_period", "date_after_period"}


def test_a_few_days_of_overhang_is_accepted():
    issues = check_period([txn(21, "10.00", "debit")], statement_start=dt.date(2025, 6, 1),
                          statement_end=dt.date(2025, 6, 20))
    assert issues == []


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------


def test_nothing_is_ever_dropped_only_flagged():
    rows = [txn(1, "100.00", "debit", "900.00"), txn(3, "25.00", "debit", "775.00")]
    rec, issues = validate_document(
        rows, account_type="bank",
        opening_balance=Decimal("1000.00"), closing_balance=Decimal("775.00"),
    )
    assert len(rows) == 2, "rows survive a failed reconciliation"
    assert all(r.needs_review for r in rows), "a document-wide failure flags every row"
    assert not rec.ok


def test_a_row_specific_issue_flags_only_that_row():
    from ingestion.validate import Issue

    rows = [txn(1, "10.00", "debit"), txn(2, "10.00", "debit")]
    flagged = flag_rows_needing_review(
        rows, [Issue(code="x", severity=ERROR, message="m", source_ref="stmt.csv#2")]
    )
    assert flagged == 1
    assert rows[0].needs_review is False
    assert rows[1].needs_review is True


def test_warnings_do_not_flag_rows():
    from ingestion.validate import Issue

    rows = [txn(1, "10.00", "debit")]
    flag_rows_needing_review(
        rows, [Issue(code="x", severity=WARNING, message="m", source_ref="stmt.csv#1")]
    )
    assert rows[0].needs_review is False


# --------------------------------------------------------------------------
# the frame gate
# --------------------------------------------------------------------------


def test_frame_gate_catches_duplicate_ids():
    from ingestion.schema import transactions_to_frame

    a = txn(1, "10.00", "debit")
    b = txn(1, "10.00", "debit")
    b.source_ref = "stmt.csv#2"
    issues = validate_frame(transactions_to_frame([a, b]))
    assert any("txn_id" in i.message for i in issues)


def test_frame_gate_passes_clean_data():
    from ingestion.schema import transactions_to_frame

    rows = [txn(1, "10.00", "debit"), txn(2, "20.00", "credit")]
    assert validate_frame(transactions_to_frame(rows)) == []
