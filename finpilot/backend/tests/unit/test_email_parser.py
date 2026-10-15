"""Email handling: what code filters out before the model is ever asked."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.extractors.email_parser import (
    EmailFields,
    RuleEmailExtractor,
    build_email_records,
    check_order_arithmetic,
    classify_email,
    is_relevant,
    parse_eml,
)
from ingestion.merchants import default_resolver


@pytest.fixture(scope="module")
def emails(synthetic_dir: Path):
    return [
        parse_eml(p, filename=f"emails/{p.name}")
        for p in sorted((synthetic_dir / "emails").glob("*.eml"))
    ]


@pytest.fixture(scope="module")
def records(emails):
    extractor = RuleEmailExtractor(resolver=default_resolver())
    return build_email_records(emails, extractor, doc_id="d_mail")


# --------------------------------------------------------------------------
# the relevance filter -- the cheapest saving there is
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("Order SWG00005001 confirmed - Rs. 491.49", "order"),
        ("Your Zomato order ZOM123", "order"),
        ("Refund processed for order MYN-1180042317", "order"),
        ("Update on your HDFC Bank A/c", "alert"),
        ("Your Netflix receipt", "receipt"),
        ("Your electricity bill is due", "bill"),
        ("OTP for your transaction", "unknown"),
        ("Your weekly deals roundup - up to 70% off!", "unknown"),
        ("You have 4 new profile views", "unknown"),
        ("The Monday Briefing", "unknown"),
    ],
)
def test_classify_email(subject, kind):
    assert classify_email("someone@example.com", subject) == kind


def test_a_promotional_mail_from_a_bank_domain_is_still_rejected():
    """The sender being a bank is not enough -- an OTP is not a transaction."""
    assert classify_email("noreply@hdfcbank.example", "OTP for your transaction") == "unknown"


def test_the_noise_emails_are_filtered_out(emails):
    assert len(emails) == 28
    relevant = [e for e in emails if is_relevant(e)]
    assert len(relevant) == 24
    rejected = {e.subject for e in emails if not is_relevant(e)}
    assert len(rejected) == 4


def test_headers_and_body_are_read(emails):
    order = next(e for e in emails if e.kind == "order" and "swiggy" in e.sender_domain)
    assert order.sender.endswith("@swiggy.example")
    assert order.sender_domain == "swiggy.example"
    assert order.sent_at is not None
    assert "Order ID" in order.text
    assert "<table" not in order.text, "HTML must be flattened before the model sees it"
    assert order.source_ref.startswith("emails/")


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def test_every_relevant_email_yields_fields(records):
    extracted, dropped = records
    assert len(extracted) == 24
    assert len(dropped) == 4


def test_an_order_email_is_read_completely(records):
    extracted, _ = records
    swiggy = next(
        r for r in extracted if r.fields.order_id == "SWG00005001"
    )
    fields = swiggy.fields
    assert fields.merchant == "Swiggy"
    assert fields.amount == Decimal("491.49")
    assert fields.direction == "debit"
    assert fields.occurred_on == dt.date(2025, 6, 4)
    assert fields.occurred_at == dt.time(8, 32)
    assert fields.reference_no == "500000005001"
    assert fields.account_last4 == "7890"
    assert fields.item_total == Decimal("506.49")
    assert fields.discount == Decimal("50.00")
    assert fields.delivery_fee == Decimal("35.00")
    assert fields.gst_amount == Decimal("23.40")
    assert fields.items


def test_a_bank_alert_gives_amount_direction_and_reference(records):
    extracted, _ = records
    alert = next(r for r in extracted if r.parsed.kind == "alert")
    assert alert.fields.amount is not None
    assert alert.fields.direction in ("debit", "credit")
    assert alert.fields.reference_no
    assert alert.fields.occurred_at is not None, "the time is why alerts are useful"


def test_the_grand_total_is_not_confused_with_the_item_total(records):
    """Every order receipt prints "Item total" above "Grand total"; a bare
    `total` match would take the wrong one."""
    extracted, _ = records
    with_both = [r for r in extracted if r.fields.item_total and r.fields.delivery_fee]
    assert with_both
    for record in with_both:
        assert record.fields.amount != record.fields.item_total


def test_no_email_in_the_fixture_set_fails_its_arithmetic(records):
    extracted, _ = records
    failures = [(r.source_ref, r.issues) for r in extracted if r.issues]
    assert not failures


def test_a_refund_email_is_a_credit(records):
    extracted, _ = records
    refunds = [r for r in extracted if r.fields.is_refund]
    assert refunds
    assert all(r.fields.direction == "credit" for r in refunds)


# --------------------------------------------------------------------------
# arithmetic is checked in code, not trusted from the extractor
# --------------------------------------------------------------------------


def test_arithmetic_check_accepts_a_consistent_order():
    fields = EmailFields(
        amount=Decimal("491.49"), item_total=Decimal("506.49"),
        discount=Decimal("50.00"), delivery_fee=Decimal("35.00"),
    )
    assert check_order_arithmetic(fields) is None


def test_arithmetic_check_rejects_an_inconsistent_one():
    fields = EmailFields(
        amount=Decimal("400.00"), item_total=Decimal("506.49"),
        discount=Decimal("50.00"), delivery_fee=Decimal("35.00"),
    )
    problem = check_order_arithmetic(fields)
    assert problem is not None
    assert "491.49" in problem and "400.00" in problem


def test_gst_is_not_added_on_top():
    """Indian receipts quote GST as a component of the amount charged."""
    fields = EmailFields(
        amount=Decimal("491.49"), item_total=Decimal("506.49"),
        discount=Decimal("50.00"), delivery_fee=Decimal("35.00"),
        gst_amount=Decimal("23.40"),
    )
    assert check_order_arithmetic(fields) is None


def test_arithmetic_check_is_skipped_when_there_is_nothing_to_check():
    assert check_order_arithmetic(EmailFields(amount=Decimal("100.00"))) is None


# --------------------------------------------------------------------------
# the schema gate applies to extractor output too
# --------------------------------------------------------------------------


def test_fields_are_normalised_on_the_way_in():
    fields = EmailFields(
        amount="1,234.567", account_last4="XXXX7890", direction="DEBIT",
    )
    assert fields.amount == Decimal("1234.57")
    assert fields.account_last4 == "7890"
    assert fields.direction == "debit"


def test_a_nonsense_direction_is_dropped_rather_than_kept():
    assert EmailFields(direction="sideways").direction is None


def test_extra_fields_from_a_model_are_ignored():
    fields = EmailFields(amount=Decimal("1"), hallucinated_field="nonsense")
    assert not hasattr(fields, "hallucinated_field")
