"""Dates, amounts and masking -- the three places Indian statement data bites."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ingestion.normalize import (
    clean_text,
    extract_last4,
    issuer_code,
    mask_account_id,
    mask_tail,
    mask_text,
    mask_upi_handle,
    parse_amount,
    parse_amount_with_direction,
    parse_date,
    parse_time,
    signed_to_direction,
    split_debit_credit,
)

# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("05/06/25", dt.date(2025, 6, 5)),  # day-first, not 6 May
        ("05/06/2025", dt.date(2025, 6, 5)),
        ("28/06/2025", dt.date(2025, 6, 28)),
        ("1 Jun 2025", dt.date(2025, 6, 1)),
        ("01-Jun-2025", dt.date(2025, 6, 1)),
        ("31 Aug 2025", dt.date(2025, 8, 31)),
        ("2025-06-05", dt.date(2025, 6, 5)),  # ISO wins over day-first
        ("05-06-2025", dt.date(2025, 6, 5)),
        ("2025/06/05", dt.date(2025, 6, 5)),
        (dt.date(2025, 6, 5), dt.date(2025, 6, 5)),
        (dt.datetime(2025, 6, 5, 14, 30), dt.date(2025, 6, 5)),
        (45813, dt.date(2025, 6, 5)),  # Excel serial
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "-", "TOTAL", "Closing Balance", "12:30"])
def test_parse_date_rejects_non_dates(raw):
    assert parse_date(raw) is None


def test_parse_date_dayfirst_can_be_switched_off():
    assert parse_date("05/06/2025", dayfirst=False) == dt.date(2025, 5, 6)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("09:14", dt.time(9, 14)),
        ("21:06:33", dt.time(21, 6, 33)),
        ("08:32 AM", dt.time(8, 32)),
        ("11:45 PM", dt.time(23, 45)),
        ("12:05 AM", dt.time(0, 5)),
        ("12:05 PM", dt.time(12, 5)),
        ("placed at 23.47 hrs", dt.time(23, 47)),
        (dt.datetime(2025, 6, 5, 9, 14, 7), dt.time(9, 14, 7)),
    ],
)
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "no time here", "99:99"])
def test_parse_time_rejects(raw):
    assert parse_time(raw) is None


# --------------------------------------------------------------------------
# amounts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,23,456.00", Decimal("123456.00")),  # Indian grouping
        ("12,34,567.89", Decimal("1234567.89")),
        ("1,234.56", Decimal("1234.56")),
        ("491.49", Decimal("491.49")),
        ("₹ 2,340.00", Decimal("2340.00")),
        ("Rs. 799.00", Decimal("799.00")),
        ("INR 1,032.24", Decimal("1032.24")),
        ("(500.00)", Decimal("-500.00")),
        ("-500.00", Decimal("-500.00")),
        ("−500.00", Decimal("-500.00")),  # unicode minus
        ("500", Decimal("500.00")),
        (1234.5, Decimal("1234.50")),
        (Decimal("17.70"), Decimal("17.70")),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "-", "nan", "N/A"])
def test_parse_amount_blank(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    ("raw", "amount", "direction"),
    [
        ("2,500.00 Dr", Decimal("2500.00"), "debit"),
        ("18,450.75 Cr", Decimal("18450.75"), "credit"),
        ("432.00 DR", Decimal("432.00"), "debit"),
        ("1,000.00 credit", Decimal("1000.00"), "credit"),
        ("-250.00", Decimal("-250.00"), "debit"),
        ("250.00", Decimal("250.00"), "credit"),
    ],
)
def test_amount_with_direction(raw, amount, direction):
    got_amount, got_direction = parse_amount_with_direction(raw)
    assert got_amount == amount
    assert got_direction == direction


def test_split_debit_credit():
    assert split_debit_credit("2,000.00", "") == (Decimal("2000.00"), "debit")
    assert split_debit_credit("", "1,25,000.00") == (Decimal("125000.00"), "credit")
    assert split_debit_credit("", "") is None
    assert split_debit_credit("0.00", "") is None  # a zero cell is not a value


def test_signed_to_direction():
    assert signed_to_direction(Decimal("-120.00")) == (Decimal("120.00"), "debit")
    assert signed_to_direction(Decimal("800.00")) == (Decimal("800.00"), "credit")


# --------------------------------------------------------------------------
# masking (principle 6)
# --------------------------------------------------------------------------


def test_mask_keeps_upi_reference_and_utr():
    """References are ledger data, not secrets -- masking must not eat them."""
    narration = "UPI-SWIGGY-SWIGGY.PAYU@HDFCBANK-HDFC0000060-500000005001-SWIGGY ORDER"
    masked = mask_text(narration)
    assert "500000005001" in masked
    assert "HDFC0000060" in masked
    assert "SWIGGY.PAYU" not in masked
    assert "XXXX@HDFCBANK" in masked

    utr = "NEFT CR-ICIC0000001-ACME TECHNOLOGIES PVT LTD-AARAV SHARMA-N00000100000000"
    assert mask_text(utr) == utr


def test_mask_keeps_counterparty_name():
    """The hyphen is a field separator in HDFC narrations; the VPA rule must
    not run backwards over the name before it."""
    masked = mask_text("UPI-RAMESH KUMAR-RAMESH.KUMAR@OKAXIS-UTIB0000123-500000000002-RENT JUNE")
    assert masked.startswith("UPI-RAMESH KUMAR-XXXX@OKAXIS-")
    assert "RENT JUNE" in masked


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "must_contain"),
    [
        ("NWD-447712XXXXXX4417-HDFC BANK ATM-ANDHERI W MUMBAI", "447712", "4417"),
        ("CC 000447712XXXXXX4417 AUTOPAY SI-TAD", "447712", "4417"),
        ("Account No 50100234567890", "50100234567890", "7890"),
        ("A/c 20012345678901", "20012345678901", "8901"),
        ("Card 4477 1234 5678 4417 used", "1234 5678", "4417"),
        ("paid to 9820045612@ybl", "9820045612", "@ybl"),
    ],
)
def test_mask_hides_identifiers(raw, must_not_contain, must_contain):
    masked = mask_text(raw)
    assert must_not_contain not in masked
    assert must_contain in masked


@pytest.mark.parametrize(
    "raw",
    [
        "UPI-SWIGGY-SWIGGY.PAYU@HDFCBANK-HDFC0000060-500000005001-SWIGGY ORDER",
        "NWD-447712XXXXXX4417-HDFC BANK ATM-ANDHERI W MUMBAI",
        "TO TRANSFER-UPI/DR/500000005009/Amazon In/ICIC/amazon@apl/Amazon Order--",
        "IMPS-500000000008-AARAV SHARMA-STAT-XXXXXXXX8901-SELF TRANSFER",
        "Account No 50100234567890 A/c 20012345678901",
    ],
)
def test_mask_is_idempotent(raw):
    once = mask_text(raw)
    assert mask_text(once) == once


def test_mask_leaves_email_addresses_alone():
    """An email address has a dot in the domain; a VPA does not. Only the VPA
    shape is a UPI id."""
    assert mask_text("write to aarav.sharma@example.com") == "write to aarav.sharma@example.com"


def test_mask_tail():
    assert mask_tail("50100234567890") == "XXXXXXXXXX7890"
    assert mask_tail("4477 1234 5678 4417") == "XXXXXXXXXXXX4417"
    assert mask_tail("123") == "123"


def test_mask_upi_handle():
    assert mask_upi_handle("ramesh.kumar@okaxis") == "@okaxis"
    assert mask_upi_handle("9820045612@YBL") == "@ybl"
    assert mask_upi_handle("nothandle") is None
    assert mask_upi_handle(None) is None


def test_mask_account_id():
    assert mask_account_id("50100234567890", "HDFC Bank") == "HDFC-XX7890"
    assert mask_account_id("20012345678901", "State Bank of India") == "SBI-XX8901"
    assert mask_account_id("4477123456784417", "HDFC Bank", is_card=True) == "CARD-XX4417"
    assert mask_account_id(None, "HDFC Bank") == "HDFC-XXXX"


def test_issuer_code():
    assert issuer_code("HDFC Bank") == "HDFC"
    assert issuer_code("State Bank of India") == "SBI"
    assert issuer_code("Some Tiny Co-operative Bank") == "SOMETINY"
    assert issuer_code(None) == "ACCT"


def test_extract_last4():
    assert extract_last4("HDFC Bank A/c XX7890") == "7890"
    assert extract_last4("no digits") is None


def test_clean_text():
    assert clean_text("  a  b \n\n\n c ") == "a b\n\nc"
    assert clean_text(None) == ""


def test_a_vpa_at_the_end_of_a_sentence_is_masked():
    """Regression: bank alerts almost always end the sentence right after the
    handle, and a guard that rejected any following dot let all of those
    through unmasked."""
    masked = mask_text("Rs. 22,000.00 debited to VPA ramesh.kumar@okaxis. UPI Ref 500000000002.")
    assert "ramesh.kumar" not in masked
    assert "XXXX@okaxis" in masked
    assert "500000000002" in masked, "the reference must survive"


@pytest.mark.parametrize(
    ("raw", "masked_expected"),
    [
        ("paid to 9820045612@ybl, thanks", True),
        ("sent to zomato@ybl)", True),
        ("handle is foo@ybl.", True),
        ("write to aarav.sharma@example.com please", False),
        ("contact support@finpilot.co.in", False),
    ],
)
def test_vpa_and_email_addresses_are_told_apart(raw, masked_expected):
    """A dot *inside* the domain means an email address, not a VPA."""
    assert (mask_text(raw) != raw) is masked_expected
