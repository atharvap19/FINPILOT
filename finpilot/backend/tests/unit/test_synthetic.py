"""The synthetic fixtures are only useful if they are stable and self-consistent.

These tests guard the *generator*, not the pipeline: they check that a second
run produces identical files, that the ground truth agrees with the files it
was derived from, and that the scenario really does contain every case the
brief asks for. If one of these fails, the golden test would fail for a reason
that has nothing to do with ingestion.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.generate_synthetic import (
    CARD_FILE,
    HDFC_FILE_A,
    HDFC_FILE_B,
    SBI_FILE,
    SHEET_FILE,
    generate,
    indian_grouping,
)
from scripts.synthetic_ledger import build_ledger


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def expected(synthetic_dir: Path) -> list[dict]:
    return _read(synthetic_dir / "expected_transactions.csv")


@pytest.fixture(scope="module")
def obligations(synthetic_dir: Path) -> list[dict]:
    return _read(synthetic_dir / "expected_obligations.csv")


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_generation_is_deterministic(tmp_path: Path):
    """Same seed, same bytes -- otherwise the golden test is a coin toss."""
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a)
    generate(b)
    names = [
        "expected_transactions.csv",
        "expected_obligations.csv",
        HDFC_FILE_A,
        HDFC_FILE_B,
        SBI_FILE,
    ]
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    for email_a in sorted((a / "emails").glob("*.eml")):
        assert email_a.read_bytes() == (b / "emails" / email_a.name).read_bytes()
    for bill_a in sorted((a / "bills").glob("*.pdf")):
        assert bill_a.read_bytes() == (b / "bills" / bill_a.name).read_bytes()


def test_ledger_is_deterministic():
    first = [(e.key, e.date, e.amount) for e in build_ledger()]
    second = [(e.key, e.date, e.amount) for e in build_ledger()]
    assert first == second


def test_indian_grouping():
    assert indian_grouping(Decimal("123456.00")) == "1,23,456.00"
    assert indian_grouping(Decimal("1234567.89")) == "12,34,567.89"
    assert indian_grouping(Decimal("999.50")) == "999.50"
    assert indian_grouping(Decimal("1234.00")) == "1,234.00"


# --------------------------------------------------------------------------
# the scenario covers what the brief asks for
# --------------------------------------------------------------------------


def test_every_required_file_exists(synthetic_dir: Path):
    for name in (HDFC_FILE_A, HDFC_FILE_B, SBI_FILE, CARD_FILE, SHEET_FILE):
        assert (synthetic_dir / name).exists(), name
    assert len(list((synthetic_dir / "emails").glob("*.eml"))) >= 25
    assert len(list((synthetic_dir / "bills").glob("*.pdf"))) == 3


def test_three_months_of_data(expected):
    dates = sorted(dt.date.fromisoformat(r["date"]) for r in expected)
    assert dates[0] == dt.date(2025, 6, 2)
    assert dates[-1] >= dt.date(2025, 8, 31)
    assert len({(d.year, d.month) for d in dates}) >= 3


def test_scenario_contains_every_required_case(expected):
    def rows(**match):
        return [r for r in expected if all(r[k] == v for k, v in match.items())]

    assert len(rows(direction="credit", merchant="Acme Technologies")) == 3, "salary"
    assert len(rows(merchant="Ramesh Kumar", counterparty_type="person")) == 3, "rent to a person"
    assert rows(merchant="Swiggy") and rows(merchant="Zomato"), "food orders"
    assert rows(merchant="BigBasket") or rows(merchant="Blinkit"), "groceries"
    assert rows(merchant="HPCL"), "fuel"
    assert rows(merchant="Netflix") and rows(merchant="Spotify") and rows(merchant="Cult.fit")
    assert len(rows(emi_flag="True")) == 1, "one EMI"
    assert len(rows(is_fee="True")) >= 3, "bank charges"
    assert len(rows(is_cash_withdrawal="True")) == 1, "ATM withdrawal"
    assert len(rows(is_refund="True")) == 1, "a refund"
    assert len(rows(counterparty_type="person")) >= 5, "payments to individuals"
    assert len(rows(account_type="cash")) == 6, "cash spends from the sheet"
    assert len(rows(source_type="email")) == 1, "an email with no statement behind it"


def test_late_night_food_orders_exist(expected):
    late = [
        r
        for r in expected
        if r["time"] and int(r["time"][:2]) in (22, 23, 0, 1) and r["merchant"] in {"Swiggy", "Zomato"}
    ]
    assert late, "segmentation needs late-night ordering to exist in the data"


def test_self_transfer_and_card_payment_are_paired(expected):
    transfers = [r for r in expected if r["is_transfer"] == "True"]
    assert len(transfers) == 4
    by_account = Counter(r["account"] for r in transfers)
    assert by_account["HDFC-XX7890"] == 2  # transfer out + card bill payment
    assert by_account["SBI-XX8901"] == 1  # transfer in
    assert by_account["CARD-XX4417"] == 1  # payment received on the card

    out = next(r for r in transfers if r["account"] == "SBI-XX8901")
    mate = next(
        r
        for r in transfers
        if r["account"] == "HDFC-XX7890" and r["amount"] == out["amount"]
    )
    assert out["date"] == mate["date"]
    assert out["direction"] != mate["direction"]


def test_refund_points_at_its_original_debit(expected):
    refund = next(r for r in expected if r["is_refund"] == "True")
    original = [
        r
        for r in expected
        if r["merchant"] == "Myntra" and r["direction"] == "debit"
    ]
    assert len(original) == 1
    assert refund["amount"] == original[0]["amount"]
    assert dt.date.fromisoformat(refund["date"]) > dt.date.fromisoformat(original[0]["date"])


def test_foreign_currency_transaction(expected):
    fx = [r for r in expected if r["original_currency"]]
    assert len(fx) == 1
    assert fx[0]["original_currency"] == "USD"
    assert Decimal(fx[0]["fx_amount"]) == Decimal("12.00")
    assert Decimal(fx[0]["amount"]) > Decimal(fx[0]["fx_amount"])


# --------------------------------------------------------------------------
# internal consistency of the ground truth
# --------------------------------------------------------------------------


def test_txn_ids_are_unique(expected):
    ids = [r["txn_id"] for r in expected]
    assert len(set(ids)) == len(ids)


def test_every_row_is_traceable(expected):
    for r in expected:
        assert "#" in r["source_ref"], r
        assert r["source_ref"] in r["linked_sources"], r


def test_overlapping_statements_are_one_row_each(expected):
    """The August overlap must appear once, carrying both source refs."""
    both = [
        r
        for r in expected
        if HDFC_FILE_A in r["linked_sources"] and HDFC_FILE_B in r["linked_sources"]
    ]
    assert len(both) == 19
    for r in both:
        assert r["source_ref"].startswith(HDFC_FILE_A), "the earlier statement wins"


def test_linked_sources_include_emails_and_sheet_rows(expected):
    with_email = [r for r in expected if "emails/" in r["linked_sources"]]
    assert len(with_email) >= 20
    enriched_by_sheet = [
        r
        for r in expected
        if SHEET_FILE in r["linked_sources"] and r["source_type"] != "spreadsheet"
    ]
    assert len(enriched_by_sheet) == 3, "three sheet rows re-describe statement transactions"
    for r in enriched_by_sheet:
        assert r["source_type"] == "bank_statement", "statement beats spreadsheet"


def test_enrichment_only_lands_on_rows_that_have_an_email(expected):
    for r in expected:
        has_email = "emails/" in r["linked_sources"] or r["source_type"] == "email"
        if not has_email:
            assert r["time"] == "", r["source_ref"]
            assert r["order_id"] == "", r["source_ref"]
            assert r["item_details"] == "", r["source_ref"]


def test_bank_rows_carry_a_running_balance(expected):
    for r in expected:
        if r["account_type"] == "bank" and r["source_type"] == "bank_statement":
            assert r["balance_after"], r["source_ref"]
        if r["account_type"] == "credit_card":
            assert r["balance_after"] == "", "card statements print no running balance"


def test_balances_reconcile_per_statement(expected):
    """Walk each statement in its own row order: every step of the running
    balance must equal the signed amount of the row that caused it."""
    by_file: dict[str, list[dict]] = {}
    for r in expected:
        if not r["balance_after"]:
            continue
        filename, _, row_no = r["source_ref"].partition("#")
        by_file.setdefault(filename, []).append((int(row_no), r))

    assert set(by_file) == {HDFC_FILE_A, HDFC_FILE_B, SBI_FILE}
    for filename, rows in by_file.items():
        rows.sort(key=lambda pair: pair[0])
        ordered = [r for _, r in rows]
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            delta = Decimal(cur["balance_after"]) - Decimal(prev["balance_after"])
            signed = Decimal(cur["amount"]) * (1 if cur["direction"] == "credit" else -1)
            assert delta == signed, f"{filename} {cur['source_ref']}"


def test_no_row_needs_review(expected):
    assert not [r for r in expected if r["needs_review"] == "True"]


def test_nothing_unmasked_leaked_into_the_ground_truth(expected):
    """No raw account number, card number or VPA local part anywhere."""
    forbidden = ("50100234567890", "20012345678901", "4477123456784417",
                 "ramesh.kumar@", "swiggy.payu@", "9820045612@")
    for r in expected:
        blob = " ".join(str(v) for v in r.values())
        for secret in forbidden:
            assert secret not in blob, f"{secret} leaked into {r['source_ref']}"


def test_reserved_columns_are_empty(expected):
    from ingestion.schema import RESERVED_COLUMNS

    for r in expected:
        for column in RESERVED_COLUMNS:
            assert r[column] == "", f"{column} belongs to a later stage"


# --------------------------------------------------------------------------
# obligations
# --------------------------------------------------------------------------


def test_obligations(obligations, expected):
    assert len(obligations) == 3
    statuses = Counter(o["status"] for o in obligations)
    assert statuses == {"paid": 2, "overdue": 1}
    ids = {r["txn_id"] for r in expected}
    for o in obligations:
        if o["status"] == "paid":
            assert o["paid_txn_id"] in ids
            paid = next(r for r in expected if r["txn_id"] == o["paid_txn_id"])
            assert Decimal(paid["amount"]) == Decimal(o["amount_due"])
        else:
            assert o["paid_txn_id"] == ""
        assert "X" in o["consumer_no"], "consumer numbers are masked"
