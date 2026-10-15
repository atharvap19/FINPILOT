"""File identity, real type, and reading the metadata block."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.detect import (
    EML,
    PDF,
    PNG,
    TEXT_CSV,
    XLSX,
    classify_text,
    detect,
    inspect_pdf,
    sha256_file,
    sniff_type,
)
from ingestion.schema import DocumentType

# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def test_hash_is_the_document_key(synthetic_dir: Path, tmp_path: Path):
    """Re-uploading the same bytes must be a no-op (principle 5)."""
    original = synthetic_dir / "hdfc_savings_jun_aug15.csv"
    copy = tmp_path / "renamed.csv"
    copy.write_bytes(original.read_bytes())
    assert sha256_file(original) == sha256_file(copy)


def test_a_changed_byte_changes_the_hash(tmp_path: Path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("Date,Amount\n01/06/2025,100", encoding="utf-8")
    b.write_text("Date,Amount\n01/06/2025,101", encoding="utf-8")
    assert sha256_file(a) != sha256_file(b)


# --------------------------------------------------------------------------
# type sniffing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hdfc_savings_jun_aug15.csv", TEXT_CSV),
        ("personal_expenses.xlsx", XLSX),
        ("hdfc_card_jul2025.pdf", PDF),
    ],
)
def test_sniff_type(synthetic_dir: Path, name, expected):
    assert sniff_type(synthetic_dir / name) == expected


def test_email_is_recognised_by_its_headers(synthetic_dir: Path):
    eml = next((synthetic_dir / "emails").glob("order_swiggy_*.eml"))
    assert sniff_type(eml) == EML


def test_extension_does_not_override_content(tmp_path: Path):
    """A PDF renamed to .csv is still a PDF."""
    liar = tmp_path / "statement.csv"
    liar.write_bytes(b"%PDF-1.7\n% fake\n")
    assert sniff_type(liar) == PDF


def test_unknown_content_falls_back_to_csv(tmp_path: Path):
    path = tmp_path / "mystery.dat"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    assert sniff_type(path) == TEXT_CSV


# --------------------------------------------------------------------------
# classification from the metadata block
# --------------------------------------------------------------------------


def test_hdfc_statement_metadata(synthetic_dir: Path):
    det = detect(synthetic_dir / "hdfc_savings_jun_aug15.csv")
    assert det.document_type is DocumentType.BANK_STATEMENT
    assert det.issuer == "HDFC Bank"
    assert det.account == "HDFC-XX7890"
    assert det.statement_start == dt.date(2025, 6, 1)
    assert det.statement_end == dt.date(2025, 8, 15)
    assert det.opening_balance == Decimal("184250.00")
    assert det.closing_balance == Decimal("386500.94")
    assert det.confidence >= 0.5


def test_sbi_statement_metadata_in_a_different_layout(synthetic_dir: Path):
    det = detect(synthetic_dir / "sbi_savings_jun_aug.csv")
    assert det.document_type is DocumentType.BANK_STATEMENT
    assert det.issuer == "State Bank of India"
    assert det.account == "SBI-XX8901"
    assert det.account_holder == "AARAV SHARMA"
    assert det.statement_start == dt.date(2025, 6, 1)
    assert det.statement_end == dt.date(2025, 8, 31)
    assert det.opening_balance == Decimal("42180.00")


def test_account_number_is_masked_not_stored_raw(synthetic_dir: Path):
    det = detect(synthetic_dir / "hdfc_savings_jun_aug15.csv")
    assert det.account is not None
    assert "50100234567890" not in det.account


def test_a_plain_sheet_is_a_spreadsheet_not_a_statement(synthetic_dir: Path):
    det = detect(synthetic_dir / "personal_expenses.xlsx")
    assert det.document_type is DocumentType.SPREADSHEET
    assert det.issuer is None


def test_a_bill_pdf_is_recognised_and_readable(synthetic_dir: Path):
    det = detect(synthetic_dir / "bills" / "bill_tatapower_jul2025.pdf")
    assert det.document_type is DocumentType.BILL
    assert det.has_text_layer is True
    assert det.issues == []


def test_an_image_is_reported_as_unreadable_not_guessed_at(tmp_path: Path):
    """This pipeline extracts with code only. A photograph has no text layer,
    so it is refused with a reason rather than silently dropped."""
    from PIL import Image

    photo = tmp_path / "bill_photo.png"
    Image.new("RGB", (60, 40), "white").save(photo)

    det = detect(photo)
    assert det.file_type == PNG
    assert det.document_type is DocumentType.UNKNOWN
    assert det.has_text_layer is False
    assert any("code only" in issue for issue in det.issues)


def test_an_image_routes_to_unsupported():
    from ingestion.detect import Detection
    from ingestion.pipeline import route

    assert route(Detection(file_hash="h", file_type=PNG)) == "unsupported"


def test_a_scanned_pdf_routes_to_unsupported():
    """No text layer means no parser can read it."""
    from ingestion.detect import PDF, Detection
    from ingestion.pipeline import route

    scan = Detection(file_hash="h", file_type=PDF, has_text_layer=False)
    assert route(scan) == "unsupported"


def test_email_classification(synthetic_dir: Path):
    eml = next((synthetic_dir / "emails").glob("order_swiggy_*.eml"))
    assert detect(eml).document_type is DocumentType.EMAIL


def test_card_statement_is_distinguished_from_a_bank_statement():
    det = classify_text(
        "HDFC BANK CREDIT CARD STATEMENT\n"
        "Card No: 4477 12XX XXXX 4417\n"
        "Statement Period: 20/06/2025 to 19/07/2025\n"
        "Payment Due Date: 07/08/2025\n"
        "Credit Limit: 4,00,000.00\n"
        "Total Dues 14,239.74\n"
        "Minimum Amount Due 712.00\n"
    )
    assert det.document_type is DocumentType.CC_STATEMENT
    assert det.is_card is True
    assert det.account == "CARD-XX4417"
    assert det.statement_start == dt.date(2025, 6, 20)
    assert det.statement_end == dt.date(2025, 7, 19)


def test_a_bill_is_recognised_by_its_labels():
    det = classify_text(
        "TATA POWER\nConsumer Number 900012345678\nBill Date 05-07-2025\n"
        "Due Date 20-07-2025\nBilling Period Jun 2025\nTotal Amount Payable Rs. 2,340.00\n"
    )
    assert det.document_type is DocumentType.BILL


def test_rules_failing_is_reported_rather_than_guessed():
    """An empty classification is the signal to fall back to the LLM."""
    det = classify_text("hello there\nnothing useful here\n")
    assert det.document_type is DocumentType.UNKNOWN
    assert det.rules_were_enough is False


# --------------------------------------------------------------------------
# PDFs
# --------------------------------------------------------------------------


def test_text_pdf_is_detected_as_having_a_text_layer(synthetic_dir: Path):
    info = inspect_pdf(synthetic_dir / "hdfc_card_jul2025.pdf")
    assert info.encrypted is False
    assert info.has_text_layer is True
    assert info.page_count == 1
    assert "CREDIT CARD STATEMENT" in info.first_page_text.upper()


def test_encrypted_pdf_is_reported_not_opened(tmp_path: Path, synthetic_dir: Path):
    import pikepdf

    locked = tmp_path / "locked.pdf"
    with pikepdf.open(synthetic_dir / "hdfc_card_jul2025.pdf") as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="o", user="secret"))

    info = inspect_pdf(locked)
    assert info.encrypted is True

    det = detect(locked)
    assert det.needs_password is True
    assert any("password" in i.lower() for i in det.issues)


def test_a_password_unlocks_but_is_never_returned(tmp_path: Path, synthetic_dir: Path):
    """Principle 6: the password is used for the call and not retained."""
    import dataclasses

    import pikepdf

    locked = tmp_path / "locked.pdf"
    with pikepdf.open(synthetic_dir / "hdfc_card_jul2025.pdf") as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="o", user="secret"))

    info = inspect_pdf(locked, password="secret")
    assert info.encrypted is False
    assert info.has_text_layer is True
    assert "secret" not in str(dataclasses.asdict(info))
