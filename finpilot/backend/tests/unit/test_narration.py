"""Narration parsing across the three statement dialects.

The table below is the specification: each row is a real narration shape and
what the ledger must get out of it.
"""

from __future__ import annotations

import pytest

from ingestion.merchants import default_resolver
from ingestion.narration import channel_for, parse_narration

HOLDER = "AARAV SHARMA"


def parse(text: str, dialect: str = "generic"):
    return parse_narration(
        text, dialect=dialect, account_holder=HOLDER,
        known_merchant=default_resolver().is_known,
    )


# --------------------------------------------------------------------------
# HDFC: hyphen-joined
# --------------------------------------------------------------------------


def test_hdfc_upi():
    p = parse("UPI-SWIGGY-SWIGGY.PAYU@HDFCBANK-HDFC0000060-500000005001-SWIGGY ORDER", "hdfc")
    assert p.payment_mode == "UPI"
    assert p.reference_no == "500000005001"
    assert p.counterparty_name == "SWIGGY"
    assert p.counterparty_upi_handle == "@hdfcbank"
    assert p.upi_remark == "SWIGGY ORDER"
    assert p.counterparty_type == "merchant"
    assert p.channel == "app"


def test_hdfc_upi_reference_is_not_eaten_by_the_card_rule():
    """A 12-digit UPI reference has the same shape as a card number; the
    reference must win, because the ledger has a column for it."""
    p = parse("UPI-A SHOP-shop@ybl-HDFC0000060-500000005001-X", "hdfc")
    assert p.reference_no == "500000005001"
    assert p.card_last4 is None


def test_hdfc_upi_to_a_person_by_phone_vpa():
    p = parse("UPI-PRIYA NAIR-9820045612@YBL-YESB0000001-500000000010-DINNER SPLIT", "hdfc")
    assert p.counterparty_type == "person"
    assert p.counterparty_upi_handle == "@ybl"
    assert p.counterparty_name == "PRIYA NAIR"


def test_hdfc_upi_to_a_person_by_name_vpa():
    p = parse("UPI-RAMESH KUMAR-RAMESH.KUMAR@OKAXIS-UTIB0000123-500000000002-RENT JUNE", "hdfc")
    assert p.counterparty_type == "person"
    assert p.counterparty_name == "RAMESH KUMAR"
    assert p.upi_remark == "RENT JUNE"


def test_hdfc_remark_keeps_its_own_hyphens():
    p = parse(
        "UPI-MYNTRA DESIGNS-MYNTRA.RZP@HDFCBANK-HDFC0000060-500000000012-"
        "MYNTRA REFUND MYN-1180042317",
        "hdfc",
    )
    assert p.upi_remark == "MYNTRA REFUND MYN-1180042317"
    assert p.reference_no == "500000000012"


def test_hdfc_neft_credit():
    p = parse(
        "NEFT CR-ICIC0000001-ACME TECHNOLOGIES PVT LTD-AARAV SHARMA-N00000100000000", "hdfc"
    )
    assert p.payment_mode == "NEFT"
    assert p.reference_no == "N00000100000000"
    assert p.counterparty_name == "ACME TECHNOLOGIES PVT LTD"
    assert p.channel == "online"


def test_hdfc_imps_puts_the_reference_first():
    """IMPS is REF-NAME-BANK-ACCOUNT-REMARK, the opposite order to UPI."""
    p = parse("IMPS-500000000008-AARAV SHARMA-STAT-XXXXXXXX8901-SELF TRANSFER", "hdfc")
    assert p.payment_mode == "IMPS"
    assert p.reference_no == "500000000008"
    assert p.counterparty_name == "AARAV SHARMA"
    assert p.is_self is True
    assert p.counterparty_type == "self"
    assert p.card_last4 is None, "the masked token is a bank account, not a card"


def test_hdfc_ach_mandate():
    p = parse("ACH D- NETFLIX ENTERTAINMENT SER-9876543200", "hdfc")
    assert p.payment_mode == "ACH"
    assert p.reference_no == "9876543200"
    assert p.counterparty_name == "NETFLIX ENTERTAINMENT SER"


def test_hdfc_atm_withdrawal():
    p = parse("NWD-447712XXXXXX4417-HDFC BANK ATM-ANDHERI W MUMBAI", "hdfc")
    assert p.payment_mode == "ATM"
    assert p.is_cash_withdrawal is True
    assert p.counterparty_type == "self"
    assert p.card_last4 == "4417"
    assert p.channel == "atm"


def test_hdfc_credit_card_autopay():
    p = parse("CC 000447712XXXXXX4417 AUTOPAY SI-TAD", "hdfc")
    assert p.is_self is True
    assert p.card_last4 == "4417"


def test_hdfc_pos():
    p = parse("POS 447712XXXXXX4417 RELIANCE RETAIL MUMBAI IN", "hdfc")
    assert p.payment_mode == "CARD"
    assert p.card_last4 == "4417"
    assert p.counterparty_name == "RELIANCE RETAIL"
    assert p.merchant_city == "MUMBAI"


def test_hdfc_fee():
    p = parse("SMS CHRG INCL GST JUN2025", "hdfc")
    assert p.is_fee is True
    assert p.counterparty_is_issuer is True
    assert p.counterparty_type == "merchant"


# --------------------------------------------------------------------------
# SBI: slash-joined
# --------------------------------------------------------------------------


def test_sbi_upi_debit():
    p = parse("TO TRANSFER-UPI/DR/500000005009/Amazon In/ICIC/amazon@apl/Amazon Order--", "sbi")
    assert p.payment_mode == "UPI"
    assert p.reference_no == "500000005009"
    assert p.counterparty_name == "Amazon In"
    assert p.counterparty_upi_handle == "@apl"
    assert p.upi_remark == "Amazon Order"
    assert p.counterparty_type == "merchant", "the dictionary knows Amazon is a shop"


def test_sbi_upi_to_a_person():
    p = parse(
        "TO TRANSFER-UPI/DR/500000000011/Vikram Singh/PYTM/8765432109@paytm/Trek Share--", "sbi"
    )
    assert p.counterparty_type == "person"
    assert p.counterparty_name == "Vikram Singh"


def test_sbi_imps_credit_from_self():
    p = parse("BY TRANSFER-INB IMPS/500000000008/Aarav Sharma/HDFC--", "sbi")
    assert p.payment_mode == "IMPS"
    assert p.reference_no == "500000000008"
    assert p.counterparty_type == "self"


def test_sbi_charges_and_interest_belong_to_the_bank():
    fee = parse("DEBIT-AMB CHARGES FOR JUL 2025", "sbi")
    assert fee.is_fee is True
    assert fee.counterparty_is_issuer is True

    interest = parse("CREDIT INTEREST", "sbi")
    assert interest.counterparty_is_issuer is True
    assert interest.is_fee is False


# --------------------------------------------------------------------------
# card statements
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name", "city", "country"),
    [
        ("SWIGGY BANGALORE IN", "SWIGGY", "BANGALORE", "IN"),
        ("HPCL PETROL PUMP MUMBAI IN", "HPCL PETROL PUMP", "MUMBAI", "IN"),
        ("AMAZON WEB SERVICES SEATTLE US", "AMAZON WEB SERVICES", "SEATTLE", "US"),
        ("DMART AVENUE SUPERMART MUMBAI IN", "DMART AVENUE SUPERMART", "MUMBAI", "IN"),
    ],
)
def test_card_merchant_city_country(text, name, city, country):
    p = parse(text, "card")
    assert p.payment_mode == "CARD"
    assert p.counterparty_name == name
    assert p.merchant_city == city
    assert p.country == country
    assert p.counterparty_type == "merchant"


def test_card_emi():
    p = parse("EMI 03/12 SAMSUNG INDIA ELECTRONICS", "card")
    assert p.emi_flag is True
    assert p.counterparty_name == "SAMSUNG INDIA ELECTRONICS"


def test_card_payment_received_is_self():
    p = parse("PAYMENT RECEIVED - THANK YOU", "card")
    assert p.is_self is True
    assert p.counterparty_type == "self"
    assert p.payment_mode == "OTHER"


# --------------------------------------------------------------------------
# generic fallback and channel
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("SOME BANK UPI PAYMENT", "UPI"),
        ("NEFT INWARD FROM XYZ", "NEFT"),
        ("RTGS OUTWARD", "RTGS"),
        ("NACH DEBIT MANDATE", "ACH"),
        ("CASH WDL AT BRANCH", "ATM"),
        ("CHEQUE CLEARING", "CHEQUE"),
        ("SOMETHING ENTIRELY UNKNOWN", "OTHER"),
    ],
)
def test_generic_mode_detection(text, mode):
    assert parse(text).payment_mode == mode


def test_unparseable_narration_reports_low_confidence():
    """Low confidence is the signal to escalate to the LLM, not to guess."""
    assert parse("XXQZ 991").confidence < 0.5


@pytest.mark.parametrize(
    ("mode", "channel"),
    [("UPI", "app"), ("CARD", "pos"), ("ATM", "atm"), ("NEFT", "online"),
     ("IMPS", "online"), ("ACH", "online"), ("CASH", "other"), ("OTHER", None)],
)
def test_channel_is_derived_from_the_instrument(mode, channel):
    assert channel_for(mode) == channel


def test_empty_narration_is_safe():
    p = parse("")
    assert p.payment_mode == "OTHER"
    assert p.counterparty_name is None
