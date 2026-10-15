"""Finding the table inside a bank export."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.detect import TEXT_CSV, XLSX
from ingestion.extractors.tabular import (
    extract_table,
    find_header_row,
    is_footer_row,
    read_grid,
    score_header_row,
)

HDFC = "hdfc_savings_jun_aug15.csv"
SBI = "sbi_savings_jun_aug.csv"
SHEET = "personal_expenses.xlsx"


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


# --------------------------------------------------------------------------
# header detection
# --------------------------------------------------------------------------


def test_finds_header_under_a_metadata_block(synthetic_dir: Path):
    """HDFC puts 23 rows of branch details above the table."""
    grid = read_grid(synthetic_dir / HDFC, TEXT_CSV)
    index, score = find_header_row(grid)
    assert index == 23
    assert score > 0.8
    assert grid[index][0] == "Date"


def test_finds_header_in_a_different_dialect(synthetic_dir: Path):
    grid = read_grid(synthetic_dir / SBI, TEXT_CSV)
    index, _ = find_header_row(grid)
    assert grid[index][0] == "Txn Date"


def test_header_at_row_zero(tmp_path: Path):
    path = write(
        tmp_path, "plain.csv",
        "Date,Description,Debit,Credit,Balance\r\n"
        "01/06/2025,UPI-SHOP-shop@ybl-HDFC0000060-500000000001-X,100.00,,900.00\r\n"
        "02/06/2025,UPI-SHOP2-shop2@ybl-HDFC0000060-500000000002-Y,,50.00,950.00\r\n",
    )
    extraction = extract_table(path, TEXT_CSV)
    assert extraction.header_row_index == 0
    assert len(extraction.rows) == 2


def test_blank_rows_do_not_hide_the_header(tmp_path: Path):
    """Some exports double-space everything; the header must still be found."""
    body = [
        "Some Bank Ltd,,,,",
        "",
        "Account No : 50100234567890,,,,",
        "",
        "Date,Narration,Debit,Credit,Balance",
        "",
        "01/06/2025,UPI-SHOP-shop@ybl-HDFC0000060-500000000001-X,100.00,,900.00",
        "",
        "02/06/2025,UPI-SHOP-shop@ybl-HDFC0000060-500000000002-Y,200.00,,700.00",
    ]
    path = write(tmp_path, "spaced.csv", "\r\n".join(body))
    extraction = extract_table(path, TEXT_CSV)
    assert extraction.headers[:2] == ["Date", "Narration"]
    assert len(extraction.rows) == 2


def test_a_data_row_is_not_mistaken_for_a_header(synthetic_dir: Path):
    grid = read_grid(synthetic_dir / HDFC, TEXT_CSV)
    assert score_header_row(grid, 24) == 0.0  # the first transaction


def test_no_table_at_all(tmp_path: Path):
    path = write(tmp_path, "prose.csv", "Dear customer,\r\nThank you for banking with us.\r\n")
    extraction = extract_table(path, TEXT_CSV)
    assert extraction.header_row_index == -1
    assert extraction.rows == []
    assert extraction.issues


# --------------------------------------------------------------------------
# footers
# --------------------------------------------------------------------------


def test_footer_rows_are_dropped(synthetic_dir: Path):
    extraction = extract_table(synthetic_dir / HDFC, TEXT_CSV)
    assert len(extraction.rows) == 83
    reasons = {reason for _, reason in extraction.dropped}
    assert "totals row" in reasons
    assert any("end of statement" in r for r in reasons)
    for row in extraction.rows:
        assert row.get("Date")


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        (["", "", ""], "blank row"),
        (["*** END OF STATEMENT ***", "", ""], "footer marker 'end of statement'"),
        (["Total", "", "1000.00"], "totals row"),
        (["not a date", "something", ""], "no parseable date"),
        (["01/06/2025", "UPI-SHOP", "100.00"], None),
    ],
)
def test_is_footer_row(cells, expected):
    assert is_footer_row(cells, date_index=0) == expected


def test_nothing_after_the_end_marker_survives(tmp_path: Path):
    body = [
        "Date,Narration,Debit,Credit,Balance",
        "01/06/2025,UPI-A-a@ybl-HDFC0000060-500000000001-X,100.00,,900.00",
        "*** END OF STATEMENT ***,,,,",
        "02/06/2025,THIS ROW IS AFTER THE END,50.00,,850.00",
    ]
    extraction = extract_table(tmp_path / "x.csv" if False else write(tmp_path, "x.csv", "\r\n".join(body)), TEXT_CSV)
    assert len(extraction.rows) == 1


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def test_ragged_rows_are_padded(tmp_path: Path):
    path = write(tmp_path, "ragged.csv", "a,b,c\r\n1,2\r\n3,4,5,6\r\n")
    grid = read_grid(path, TEXT_CSV)
    assert {len(r) for r in grid} == {4}


def test_tab_delimited(tmp_path: Path):
    body = "Date\tNarration\tDebit\tCredit\tBalance\r\n01/06/2025\tUPI-A-a@ybl-HDFC0000060-500000000001-X\t100.00\t\t900.00\r\n"
    extraction = extract_table(write(tmp_path, "tabbed.csv", body), TEXT_CSV)
    assert extraction.headers[0] == "Date"
    assert len(extraction.rows) == 1


def test_quoted_commas_survive(tmp_path: Path):
    body = (
        "Date,Narration,Debit,Credit,Balance\r\n"
        '01/06/2025,"PAID TO SHOP, MUMBAI",100.00,,900.00\r\n'
    )
    extraction = extract_table(write(tmp_path, "quoted.csv", body), TEXT_CSV)
    assert extraction.rows[0].get("Narration") == "PAID TO SHOP, MUMBAI"


def test_excel_dates_and_numbers_survive(synthetic_dir: Path):
    extraction = extract_table(synthetic_dir / SHEET, XLSX)
    assert extraction.headers == ["Date", "Description", "Amount", "Mode", "Note"]
    assert len(extraction.rows) == 9
    assert extraction.rows[0].get("Amount") == "-120"


def test_duplicate_and_blank_headers_are_disambiguated(tmp_path: Path):
    body = (
        "Date,Amount,Amount,,Balance\r\n"
        "01/06/2025,100.00,200.00,x,900.00\r\n"
        "02/06/2025,100.00,200.00,x,800.00\r\n"
        "03/06/2025,100.00,200.00,x,700.00\r\n"
    )
    extraction = extract_table(write(tmp_path, "dupes.csv", body), TEXT_CSV)
    assert extraction.headers == ["Date", "Amount", "Amount_2", "column_4", "Balance"]


# --------------------------------------------------------------------------
# traceability
# --------------------------------------------------------------------------


def test_source_refs_are_sequential_and_named(synthetic_dir: Path):
    extraction = extract_table(synthetic_dir / SBI, TEXT_CSV)
    assert extraction.rows[0].source_ref == f"{SBI}#1"
    assert extraction.rows[-1].source_ref == f"{SBI}#{len(extraction.rows)}"


def test_sample_rows_for_the_mapping_dialog(synthetic_dir: Path):
    extraction = extract_table(synthetic_dir / SHEET, XLSX)
    sample = extraction.sample_rows
    assert len(sample) == 9  # fewer than ten rows in the file
    assert all(len(r) == len(extraction.headers) for r in sample)


# --------------------------------------------------------------------------
# files that are tables, but not of transactions
# --------------------------------------------------------------------------


def budget_sheet(tmp_path: Path) -> Path:
    """A monthly budget: categories and totals, and no dates anywhere."""
    from openpyxl import Workbook

    path = tmp_path / "monthly_budget.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Category", "Monthly Budget (INR)", "Actual Spend (INR)", "Notes"])
    for category, budgeted, actual, note in [
        ("Rent", 22000, 22000, "Monthly apartment rent"),
        ("Groceries", 6000, 4850, "Food and household"),
        ("Transport", 5000, 3340, "Fuel and cabs"),
        ("Dining", 4000, 3130, "Restaurants"),
    ]:
        ws.append([category, budgeted, actual, note])
    wb.save(path)
    return path


def test_a_budget_sheet_yields_no_transactions(tmp_path: Path):
    """Categories and totals are not transactions, and inventing eleven dateless
    rows from them would be worse than importing nothing."""
    extraction = extract_table(budget_sheet(tmp_path), XLSX)
    assert extraction.rows == []


def test_the_reason_says_what_is_actually_wrong(tmp_path: Path):
    """"could not find a header" is unhelpful when the file plainly has one --
    what it lacks is a date column."""
    extraction = extract_table(budget_sheet(tmp_path), XLSX)
    reason = extraction.issues[0]
    assert "summary rather than a list of transactions" in reason
    assert "no dates" in reason
    assert "Category" in reason, "the reason names the columns it did find"


def test_a_file_with_no_transactions_is_not_a_failure(tmp_path: Path):
    """Nothing went wrong and there is nothing for the user to fix, so it must
    not be reported as a failed document."""
    from ingestion.pipeline import process_tabular_document

    result = process_tabular_document(budget_sheet(tmp_path), doc_id="d_budget")
    assert result.status == "no_transactions"
    assert result.error_count == 0
    assert result.warning_count == 1


def test_a_genuinely_unreadable_file_still_reports_no_table(tmp_path: Path):
    path = tmp_path / "letter.csv"
    path.write_text("Dear customer,\r\nThank you for banking with us.\r\n",
                    encoding="utf-8", newline="")
    extraction = extract_table(path, TEXT_CSV)
    assert extraction.rows == []
    assert "no table was found" in extraction.issues[0]
