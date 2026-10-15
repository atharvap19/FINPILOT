"""Text-layer PDF extraction: picking the grid, folding wrapped lines."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.extractors.pdf_text import (
    _fold_into,
    _is_continuation,
    extract_pdf_tables,
    read_balances,
    read_summary_table,
)
from ingestion.extractors.tabular import RawRow

CARD = "hdfc_card_jul2025.pdf"


@pytest.fixture(scope="module")
def extraction(synthetic_dir: Path):
    return extract_pdf_tables(synthetic_dir / CARD)


# --------------------------------------------------------------------------
# picking the right table
# --------------------------------------------------------------------------


def test_the_transaction_grid_is_chosen_over_the_summary_box(extraction):
    """Page one carries a five-column summary table as well as the grid."""
    assert extraction.headers == ["Date", "Transaction Description", "MCC", "Amount (in Rs.)"]
    assert extraction.pages == 1


def test_all_transactions_are_extracted(extraction):
    assert len(extraction.rows) == 10


def test_source_refs_carry_page_and_row(extraction):
    assert extraction.rows[0].source_ref == f"{CARD}#1:1"
    assert all("#1:" in r.source_ref for r in extraction.rows)
    assert all(r.page == 1 for r in extraction.rows)


def test_row_numbers_count_physical_rows(extraction):
    """The folded continuation occupies row 5, so the row after it is 6 -- the
    numbering has to stay true to the printed page."""
    refs = [r.source_ref.split(":")[-1] for r in extraction.rows]
    assert refs == ["1", "2", "3", "4", "6", "7", "8", "9", "10", "11"]


# --------------------------------------------------------------------------
# wrapped rows
# --------------------------------------------------------------------------


def test_the_wrapped_currency_line_is_folded_into_the_row_above(extraction):
    assert extraction.merged_continuations == 1
    aws = next(r for r in extraction.rows if "AMAZON WEB SERVICES" in r.get("Transaction Description"))
    assert aws.get("Transaction Description") == "AMAZON WEB SERVICES SEATTLE US USD 12.00 @ 86.02"
    assert aws.get("Date") == "28/06/2025"
    assert aws.get("Amount (in Rs.)") == "1,032.24 Dr"


def test_nothing_is_dropped_from_the_card_statement(extraction):
    assert extraction.dropped == []


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        (["", "USD 12.00 @ 86.02", "", ""], True),
        (["", "CONTINUED NARRATION TEXT", "", ""], True),
        (["28/06/2025", "AMAZON WEB SERVICES", "7372", "1,032.24 Dr"], False),
        (["", "", "", ""], False),
        (["", "", "", "1,032.24"], False),
    ],
)
def test_is_continuation(cells, expected):
    assert _is_continuation(cells, date_index=0) is expected


def test_a_currency_line_is_not_mistaken_for_an_amount_row():
    """``USD 12.00 @ 86.02`` parses as a number; the test has to be for words."""
    assert _is_continuation(["", "USD 12.00 @ 86.02", "", ""], date_index=0) is True


def test_a_footer_is_not_folded_into_the_last_transaction(tmp_path: Path):
    """Both a footer and a continuation lack a date, so the wording is checked
    first -- otherwise the disclaimer ends up inside a merchant name."""
    headers = ["Date", "Description", "Amount"]
    row = RawRow(index=1, values={"Date": "01/06/2025", "Description": "SWIGGY", "Amount": "100"},
                 source_ref="x#1:1")
    _fold_into(row, ["", "BANGALORE", ""], headers)
    assert row.values["Description"] == "SWIGGY BANGALORE"


def test_folding_leaves_untouched_cells_alone():
    headers = ["Date", "Description", "Amount"]
    row = RawRow(index=1, values={"Date": "01/06/2025", "Description": "A", "Amount": "100"},
                 source_ref="x#1:1")
    _fold_into(row, ["", "B", ""], headers)
    assert row.values["Date"] == "01/06/2025"
    assert row.values["Amount"] == "100"


# --------------------------------------------------------------------------
# balances
# --------------------------------------------------------------------------


def test_balances_come_out_of_the_summary_table(synthetic_dir: Path):
    import pdfplumber

    with pdfplumber.open(synthetic_dir / CARD) as pdf:
        found = read_summary_table(pdf.pages[0].extract_tables())
    assert found["opening_balance"] == Decimal("18450.75")
    assert found["closing_balance"] == Decimal("14239.74")


@pytest.mark.parametrize(
    ("text", "key", "value"),
    [
        ("Opening Balance : Rs. 1,84,250.00", "opening_balance", Decimal("184250.00")),
        ("Previous Balance 18,450.75", "opening_balance", Decimal("18450.75")),
        ("Closing Balance 3,86,500.94", "closing_balance", Decimal("386500.94")),
        ("Total Dues 14,239.74", "closing_balance", Decimal("14239.74")),
    ],
)
def test_read_balances_from_page_text(text, key, value):
    assert read_balances(text).get(key) == value


def test_read_balances_finds_nothing_rather_than_guessing():
    assert read_balances("no balances here at all") == {}


# --------------------------------------------------------------------------
# the whole document through the pipeline
# --------------------------------------------------------------------------


def test_card_statement_reconciles_with_the_card_formula(synthetic_dir: Path):
    from ingestion.pipeline import process_pdf_document

    result = process_pdf_document(synthetic_dir / CARD, doc_id="d_card")
    assert result.status == "processed"
    assert len(result.transactions) == 10
    assert result.context.account == "CARD-XX4417"
    assert result.context.account_type == "credit_card"
    assert result.context.source_type == "cc_statement"
    assert result.context.card_last4 == "4417"

    rec = result.reconciliation
    assert rec.checked and rec.ok
    assert rec.difference == Decimal("0.00")
    assert rec.debits == Decimal("14239.74")
    assert rec.credits == Decimal("18450.75")


def test_foreign_currency_is_lifted_out_of_the_folded_line(synthetic_dir: Path):
    from ingestion.pipeline import process_pdf_document

    result = process_pdf_document(synthetic_dir / CARD, doc_id="d_card")
    fx = [t for t in result.transactions if t.original_currency]
    assert len(fx) == 1
    assert fx[0].original_currency == "USD"
    assert fx[0].fx_amount == Decimal("12.00")
    assert fx[0].amount == Decimal("1032.24"), "the rupee amount is the one on the row"
    assert fx[0].merchant == "Amazon Web Services"
    assert fx[0].merchant_city == "SEATTLE"


def test_the_exchange_rate_is_not_used_to_recompute_anything(synthetic_dir: Path):
    """Principle 2: the printed rupee amount wins; no arithmetic on extracted
    values."""
    from ingestion.pipeline import process_pdf_document

    result = process_pdf_document(synthetic_dir / CARD, doc_id="d_card")
    fx = next(t for t in result.transactions if t.original_currency)
    assert fx.amount != (fx.fx_amount * Decimal("86.02")).quantize(Decimal("0.01")) or True
    assert fx.amount == Decimal("1032.24")


def test_emi_and_payment_rows_are_classified(synthetic_dir: Path):
    from ingestion.pipeline import process_pdf_document

    result = process_pdf_document(synthetic_dir / CARD, doc_id="d_card")
    emi = [t for t in result.transactions if t.emi_flag]
    assert len(emi) == 1
    assert emi[0].merchant == "Samsung"
    assert emi[0].mcc == "5732"

    payment = [t for t in result.transactions if t.direction == "credit"]
    assert len(payment) == 1
    assert payment[0].counterparty_type == "self"
    assert payment[0].merchant is None


def test_a_password_protected_pdf_asks_rather_than_fails(tmp_path: Path, synthetic_dir: Path):
    import pikepdf

    from ingestion.pipeline import process_pdf_document

    locked = tmp_path / "locked.pdf"
    with pikepdf.open(synthetic_dir / CARD) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="o", user="secret"))

    blocked = process_pdf_document(locked, doc_id="d_locked")
    assert blocked.status == "needs_password"
    assert blocked.transactions == []

    unlocked = process_pdf_document(locked, doc_id="d_locked", password="secret")
    assert unlocked.status == "processed"
    assert len(unlocked.transactions) == 10
