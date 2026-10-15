"""The six linking passes, each on its own."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ingestion.extractors.bills import BillFields, BillRecord
from ingestion.extractors.email_parser import EmailFields, EmailItem, EmailRecord, ParsedEmail
from ingestion.link import (
    AmountIndex,
    LinkSettings,
    LinkStats,
    link_bills,
    link_emails,
    link_refunds,
    link_spreadsheet_rows,
    link_transfers,
)
from ingestion.merge import LinkRegistry
from ingestion.schema import Transaction, make_txn_id

SETTINGS = LinkSettings()


def txn(
    *,
    day: int,
    amount: str,
    direction: str = "debit",
    account: str = "HDFC-XX7890",
    account_type: str = "bank",
    merchant: str | None = None,
    counterparty_type: str = "merchant",
    source_type: str = "bank_statement",
    reference_no: str | None = None,
    month: int = 6,
    **kw,
) -> Transaction:
    date = dt.date(2025, month, day)
    payload = dict(
        txn_id=make_txn_id(
            account=account, date=date, amount=Decimal(amount), direction=direction,
            reference_no=reference_no, raw_description=kw.get("raw_description", merchant or "x"),
        ),
        date=date,
        amount=Decimal(amount),
        direction=direction,
        account=account,
        account_type=account_type,
        source_type=source_type,
        source_ref=f"{account}.csv#{month}{day}",
        raw_description=merchant or "x",
        merchant=merchant,
        counterparty_type=counterparty_type,
        reference_no=reference_no,
        extraction_method="template",
    )
    payload.update(kw)
    return Transaction(**payload)


# --------------------------------------------------------------------------
# the amount index
# --------------------------------------------------------------------------


def test_amount_index_finds_values_within_tolerance():
    rows = [txn(day=1, amount="491.49"), txn(day=2, amount="492.30"),
            txn(day=3, amount="600.00")]
    index = AmountIndex(rows)
    near = index.near(Decimal("491.49"), Decimal("1.00"))
    assert {str(t.amount) for t in near} == {"491.49", "492.30"}


def test_amount_index_spans_the_rupee_boundary():
    """491.90 and 492.30 differ by 0.40 but land in different rupee buckets."""
    index = AmountIndex([txn(day=1, amount="492.30")])
    assert index.near(Decimal("491.90"), Decimal("1.00"))


def test_amount_index_excludes_beyond_tolerance():
    index = AmountIndex([txn(day=1, amount="493.00")])
    assert index.near(Decimal("491.49"), Decimal("1.00")) == []


# --------------------------------------------------------------------------
# 1 + 2: transfers
# --------------------------------------------------------------------------


def test_a_self_transfer_between_two_banks():
    out = txn(day=10, amount="15000.00", direction="debit", account="HDFC-XX7890")
    incoming = txn(day=10, amount="15000.00", direction="credit", account="SBI-XX8901")
    stats = LinkStats()
    link_transfers([out, incoming], settings=SETTINGS, stats=stats)
    assert out.is_transfer and incoming.is_transfer
    assert out.counterparty_type == "self" and incoming.counterparty_type == "self"
    assert out.merchant is None
    assert stats.transfers_found == 1


def test_a_transfer_a_day_later_still_matches():
    out = txn(day=10, amount="15000.00", direction="debit", account="HDFC-XX7890")
    incoming = txn(day=11, amount="15000.00", direction="credit", account="SBI-XX8901")
    link_transfers([out, incoming], settings=SETTINGS, stats=LinkStats())
    assert out.is_transfer and incoming.is_transfer


def test_a_transfer_a_week_later_does_not():
    out = txn(day=10, amount="15000.00", direction="debit", account="HDFC-XX7890")
    incoming = txn(day=17, amount="15000.00", direction="credit", account="SBI-XX8901")
    link_transfers([out, incoming], settings=SETTINGS, stats=LinkStats())
    assert not out.is_transfer and not incoming.is_transfer


def test_same_account_is_never_a_transfer():
    out = txn(day=10, amount="500.00", direction="debit")
    incoming = txn(day=10, amount="500.00", direction="credit")
    link_transfers([out, incoming], settings=SETTINGS, stats=LinkStats())
    assert not out.is_transfer


def test_different_amounts_are_not_a_transfer():
    out = txn(day=10, amount="15000.00", direction="debit", account="HDFC-XX7890")
    incoming = txn(day=10, amount="14999.00", direction="credit", account="SBI-XX8901")
    link_transfers([out, incoming], settings=SETTINGS, stats=LinkStats())
    assert not out.is_transfer


def test_a_credit_card_bill_payment_is_counted_separately():
    bank = txn(day=2, amount="18450.75", direction="debit", account="HDFC-XX7890", month=7)
    card = txn(day=2, amount="18450.75", direction="credit", account="CARD-XX4417",
               account_type="credit_card", source_type="cc_statement", month=7,
               counterparty_type="self")
    stats = LinkStats()
    link_transfers([bank, card], settings=SETTINGS, stats=stats)
    assert bank.is_transfer and card.is_transfer
    assert stats.card_payments_found == 1
    assert stats.transfers_found == 0


def test_a_card_payment_posting_two_days_later_still_matches():
    """Card payments post with a lag, so they get a wider window than a bank
    to bank transfer."""
    bank = txn(day=2, amount="18450.75", direction="debit", account="HDFC-XX7890", month=7)
    card = txn(day=4, amount="18450.75", direction="credit", account="CARD-XX4417",
               account_type="credit_card", source_type="cc_statement", month=7)
    link_transfers([bank, card], settings=SETTINGS, stats=LinkStats())
    assert bank.is_transfer and card.is_transfer


def test_a_shared_reference_breaks_a_tie():
    out = txn(day=10, amount="15000.00", direction="debit", account="HDFC-XX7890",
              reference_no="500000000008")
    wrong = txn(day=10, amount="15000.00", direction="credit", account="SBI-XX8901",
                reference_no="999999999999")
    right = txn(day=10, amount="15000.00", direction="credit", account="ICICI-XX1111",
                reference_no="500000000008")
    link_transfers([out, wrong, right], settings=SETTINGS, stats=LinkStats())
    assert right.is_transfer
    assert not wrong.is_transfer


def test_one_credit_cannot_settle_two_debits():
    a = txn(day=10, amount="1000.00", direction="debit", account="HDFC-XX7890",
            reference_no="1")
    b = txn(day=10, amount="1000.00", direction="debit", account="HDFC-XX7890",
            reference_no="2")
    incoming = txn(day=10, amount="1000.00", direction="credit", account="SBI-XX8901")
    stats = LinkStats()
    link_transfers([a, b, incoming], settings=SETTINGS, stats=stats)
    assert stats.transfers_found == 1
    assert sum(1 for t in (a, b) if t.is_transfer) == 1


# --------------------------------------------------------------------------
# 3: refunds
# --------------------------------------------------------------------------


def test_a_refund_points_at_the_original_debit():
    bought = txn(day=4, amount="2499.00", direction="debit", merchant="Myntra", month=7)
    refunded = txn(day=12, amount="2499.00", direction="credit", merchant="Myntra", month=7)
    stats = LinkStats()
    link_refunds([bought, refunded], settings=SETTINGS, stats=stats)
    assert refunded.is_refund
    assert refunded.refund_of == bought.txn_id
    assert not bought.is_refund
    assert stats.refunds_found == 1


def test_a_partial_refund_matches():
    bought = txn(day=4, amount="2499.00", direction="debit", merchant="Myntra", month=7)
    refunded = txn(day=12, amount="899.00", direction="credit", merchant="Myntra", month=7)
    link_refunds([bought, refunded], settings=SETTINGS, stats=LinkStats())
    assert refunded.is_refund


def test_a_credit_larger_than_the_debit_is_not_a_refund():
    bought = txn(day=4, amount="899.00", direction="debit", merchant="Myntra", month=7)
    credited = txn(day=12, amount="2499.00", direction="credit", merchant="Myntra", month=7)
    link_refunds([bought, credited], settings=SETTINGS, stats=LinkStats())
    assert not credited.is_refund


def test_a_refund_must_come_after_the_debit():
    bought = txn(day=12, amount="2499.00", direction="debit", merchant="Myntra", month=7)
    credited = txn(day=4, amount="2499.00", direction="credit", merchant="Myntra", month=7)
    link_refunds([bought, credited], settings=SETTINGS, stats=LinkStats())
    assert not credited.is_refund


def test_a_different_merchant_is_not_a_refund():
    bought = txn(day=4, amount="2499.00", direction="debit", merchant="Myntra", month=7)
    credited = txn(day=12, amount="2499.00", direction="credit", merchant="Swiggy", month=7)
    link_refunds([bought, credited], settings=SETTINGS, stats=LinkStats())
    assert not credited.is_refund


def test_a_repayment_from_a_person_is_not_a_refund():
    """A friend paying back half of dinner is settling up, not a refund."""
    paid = txn(day=21, amount="1200.00", direction="debit", merchant="Priya Nair",
               counterparty_type="person")
    back = txn(day=9, amount="800.00", direction="credit", merchant="Priya Nair",
               counterparty_type="person", month=8)
    stats = LinkStats()
    link_refunds([paid, back], settings=SETTINGS, stats=stats)
    assert not back.is_refund
    assert stats.refunds_found == 0


def test_a_transfer_credit_is_never_treated_as_a_refund():
    bought = txn(day=4, amount="15000.00", direction="debit", merchant="Myntra", month=7)
    incoming = txn(day=12, amount="15000.00", direction="credit", merchant="Myntra",
                   month=7, is_transfer=True)
    link_refunds([bought, incoming], settings=SETTINGS, stats=LinkStats())
    assert not incoming.is_refund


def test_an_order_id_in_the_narration_links_a_refund():
    bought = txn(day=4, amount="2499.00", direction="debit", merchant="Myntra",
                 order_id="MYN-1180042317", month=7)
    refunded = txn(day=12, amount="2499.00", direction="credit", merchant=None, month=7,
                   counterparty_type="unknown",
                   raw_description="UPI REFUND MYN-1180042317")
    link_refunds([bought, refunded], settings=SETTINGS, stats=LinkStats())
    assert refunded.is_refund
    assert refunded.refund_of == bought.txn_id


# --------------------------------------------------------------------------
# 4: emails
# --------------------------------------------------------------------------


def email_record(
    *,
    amount: str,
    day: int,
    month: int = 6,
    merchant: str | None = "Swiggy",
    reference_no: str | None = None,
    order_id: str | None = "SWG1",
    time: dt.time | None = dt.time(20, 35),
    account_last4: str | None = "7890",
    items: list[EmailItem] | None = None,
    **kw,
) -> EmailRecord:
    parsed = ParsedEmail(
        source_ref=f"emails/{order_id or 'x'}.eml#1",
        message_id="<m@x>",
        sender="noreply@swiggy.example",
        sender_domain="swiggy.example",
        subject=f"Order {order_id} confirmed",
        sent_at=dt.datetime(2025, month, day, 20, 35),
        text="",
        kind="order",
    )
    fields = EmailFields(
        merchant=merchant,
        amount=Decimal(amount),
        direction="debit",
        occurred_on=dt.date(2025, month, day),
        occurred_at=time,
        reference_no=reference_no,
        order_id=order_id,
        account_last4=account_last4,
        items=items or [],
        **kw,
    )
    return EmailRecord(parsed=parsed, fields=fields, doc_id="d_mail")


def _link_emails(rows, records, horizons=None):
    registry = LinkRegistry()
    for row in rows:
        registry.add(row, doc_id="d_1", source_ref=row.source_ref, link_type="primary")
    stats = LinkStats()
    new_rows, items, issues = link_emails(
        rows, records, registry=registry, settings=SETTINGS, stats=stats,
        latest_statement_end=horizons or {"HDFC-XX7890": dt.date(2025, 8, 31)},
    )
    return new_rows, items, issues, stats, registry


def test_an_email_matches_by_reference_exactly():
    row = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="500000005001")
    record = email_record(amount="491.49", day=4, reference_no="500000005001")
    _, _, _, stats, registry = _link_emails([row], [record])
    assert stats.emails_linked == 1
    assert record.source_ref in row.linked_sources
    assert registry.for_txn(row.txn_id)[-1].match_score == 1.0


def test_a_reference_match_wins_even_when_the_amount_drifted():
    row = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="500000005001")
    record = email_record(amount="999.00", day=4, reference_no="500000005001")
    _, _, _, stats, _ = _link_emails([row], [record])
    assert stats.emails_linked == 1


def test_an_email_matches_on_amount_date_and_merchant():
    row = txn(day=4, amount="491.49", merchant="Swiggy")
    record = email_record(amount="491.49", day=5)
    _, _, _, stats, registry = _link_emails([row], [record])
    assert stats.emails_linked == 1
    assert registry.for_txn(row.txn_id)[-1].match_score < 1.0


def test_a_rupee_of_drift_is_tolerated():
    row = txn(day=4, amount="491.49", merchant="Swiggy")
    _, _, _, stats, _ = _link_emails([row], [email_record(amount="492.30", day=4)])
    assert stats.emails_linked == 1


def test_more_than_a_rupee_is_not():
    row = txn(day=4, amount="491.49", merchant="Swiggy")
    _, _, _, stats, _ = _link_emails([row], [email_record(amount="495.00", day=4)])
    assert stats.emails_linked == 0


def test_a_different_merchant_blocks_a_match():
    row = txn(day=4, amount="491.49", merchant="Zomato")
    _, _, _, stats, _ = _link_emails([row], [email_record(amount="491.49", day=4)])
    assert stats.emails_linked == 0


def test_an_email_enriches_without_overwriting():
    row = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="R1")
    record = email_record(
        amount="491.49", day=4, reference_no="R1",
        items=[EmailItem(name="Veg Biryani", quantity=Decimal("1"), amount=Decimal("229.00"))],
        gst_amount=Decimal("23.40"), delivery_fee=Decimal("35.00"),
    )
    _, items, _, _, _ = _link_emails([row], [record])
    assert row.time == dt.time(20, 35)
    assert row.order_id == "SWG1"
    assert row.item_details == "Veg Biryani"
    assert row.gst_amount == Decimal("23.40")
    assert row.delivery_fee == Decimal("35.00")
    assert row.amount == Decimal("491.49"), "the statement's amount is untouched"
    assert row.merchant == "Swiggy"
    assert len(items) == 1 and items[0].txn_id == row.txn_id


def test_one_email_cannot_match_two_transactions():
    a = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="A")
    b = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="B")
    records = [email_record(amount="491.49", day=4, order_id="S1"),
               email_record(amount="491.49", day=4, order_id="S2")]
    _, _, _, stats, _ = _link_emails([a, b], records)
    assert stats.emails_linked == 2
    assert "S1" in a.linked_sources or "S2" in a.linked_sources
    assert a.linked_sources != b.linked_sources


def test_an_email_after_the_statements_becomes_a_new_row():
    row = txn(day=4, amount="491.49", merchant="Swiggy")
    record = email_record(amount="512.00", day=2, month=9, order_id="SWG9")
    new_rows, _, _, stats, _ = _link_emails([row], [record])
    assert stats.emails_added == 1
    assert len(new_rows) == 1
    created = new_rows[0]
    assert created.source_type == "email"
    assert created.extraction_method == "email"
    assert created.account == "HDFC-XX7890", "picked from the account the email names"
    assert created.date == dt.date(2025, 9, 2)


def test_an_unmatched_email_inside_the_covered_period_is_not_invented():
    """The statement would have shown it, so a non-match means the matching
    failed -- inventing a row would double-count real spending."""
    row = txn(day=4, amount="491.49", merchant="Swiggy")
    record = email_record(amount="389.00", day=14, month=7, order_id="ZOM1")
    new_rows, _, issues, stats, _ = _link_emails([row], [record])
    assert new_rows == []
    assert stats.emails_added == 0
    assert stats.emails_unmatched == 1
    assert any(i.code == "email_unmatched" for i in issues)


def test_an_email_whose_arithmetic_failed_is_linked_but_flagged():
    row = txn(day=4, amount="491.49", merchant="Swiggy", reference_no="R1")
    record = email_record(amount="491.49", day=4, reference_no="R1")
    record.issues = ["order total does not add up"]
    _, _, issues, stats, _ = _link_emails([row], [record])
    assert stats.emails_linked == 1
    assert any(i.code == "email_arithmetic" for i in issues)


# --------------------------------------------------------------------------
# 5: spreadsheet rows
# --------------------------------------------------------------------------


def _link_sheet(rows):
    registry = LinkRegistry()
    for row in rows:
        registry.add(row, doc_id="d_1", source_ref=row.source_ref, link_type="primary")
    stats = LinkStats()
    kept, issues = link_spreadsheet_rows(
        rows, registry=registry, settings=SETTINGS, stats=stats,
        doc_id_by_source={"sheet.xlsx": "d_sheet"},
    )
    return kept, stats, registry


def test_a_sheet_row_describing_a_statement_row_is_folded_in():
    statement = txn(day=5, amount="22000.00", merchant="Ramesh Kumar",
                    counterparty_type="person", month=7)
    sheet = txn(day=5, amount="22000.00", month=7, account="CASH", account_type="cash",
                source_type="spreadsheet", merchant=None, counterparty_type="unknown",
                extraction_method="spreadsheet", raw_description="Rent July (from bank)")
    sheet.source_ref = "sheet.xlsx#5"
    kept, stats, _ = _link_sheet([statement, sheet])
    assert stats.spreadsheet_linked == 1
    assert sheet not in kept
    assert "sheet.xlsx#5" in statement.linked_sources
    assert statement.source_type == "bank_statement", "statement beats spreadsheet"


def test_a_genuine_cash_spend_survives():
    statement = txn(day=5, amount="22000.00", merchant="Ramesh Kumar", month=7)
    sheet = txn(day=7, amount="120.00", account="CASH", account_type="cash",
                source_type="spreadsheet", merchant=None, counterparty_type="unknown",
                extraction_method="spreadsheet", raw_description="Chai and vada pav")
    sheet.source_ref = "sheet.xlsx#1"
    kept, stats, _ = _link_sheet([statement, sheet])
    assert stats.spreadsheet_kept == 1
    assert sheet in kept
    assert sheet.account_type == "cash"


def test_a_sheet_row_never_matches_the_opposite_direction():
    statement = txn(day=5, amount="500.00", direction="credit", merchant="X", month=7)
    sheet = txn(day=5, amount="500.00", direction="debit", month=7, account="CASH",
                account_type="cash", source_type="spreadsheet",
                counterparty_type="unknown", extraction_method="spreadsheet")
    sheet.source_ref = "sheet.xlsx#1"
    _, stats, _ = _link_sheet([statement, sheet])
    assert stats.spreadsheet_linked == 0


# --------------------------------------------------------------------------
# 6: bills
# --------------------------------------------------------------------------


def bill_record(
    *, biller="Tata Power", amount="2340.00", bill_day=5, due_day=20, month=7
) -> BillRecord:
    return BillRecord(
        fields=BillFields(
            biller=biller,
            category_hint="electricity",
            amount_due=Decimal(amount),
            bill_date=dt.date(2025, month, bill_day),
            due_date=dt.date(2025, month, due_day),
            consumer_no="XXXXXXXX5678",
        ),
        doc_id="d_bill",
        source_ref=f"bills/{biller}.pdf#1:1",
    )


def _link_bills(rows, bills, as_of=dt.date(2025, 8, 31)):
    registry = LinkRegistry()
    stats = LinkStats()
    obligations, issues = link_bills(
        rows, bills, registry=registry, settings=SETTINGS, stats=stats, as_of=as_of
    )
    return obligations, stats, registry, issues


def test_a_bill_becomes_an_obligation():
    obligations, stats, _, _ = _link_bills([], [bill_record()])
    assert len(obligations) == 1
    assert obligations[0].biller == "Tata Power"
    assert obligations[0].amount_due == Decimal("2340.00")
    assert stats.bills_found == 1


def test_a_matching_debit_marks_a_bill_paid():
    payment = txn(day=18, amount="2340.00", merchant="Tata Power", month=7)
    obligations, stats, registry, _ = _link_bills([payment], [bill_record()])
    assert obligations[0].status == "paid"
    assert obligations[0].paid_txn_id == payment.txn_id
    assert stats.bills_paid == 1
    assert "bills/Tata Power.pdf#1:1" in payment.linked_sources


def test_an_unpaid_bill_past_its_due_date_is_overdue():
    obligations, stats, _, _ = _link_bills([], [bill_record(month=8, bill_day=3, due_day=18)])
    assert obligations[0].status == "overdue"
    assert obligations[0].paid_txn_id is None
    assert stats.bills_paid == 0


def test_an_unpaid_bill_not_yet_due_is_simply_due():
    obligations, _, _, _ = _link_bills(
        [], [bill_record(month=8, bill_day=3, due_day=28)], as_of=dt.date(2025, 8, 20)
    )
    assert obligations[0].status == "due"


def test_a_payment_to_a_different_biller_does_not_count():
    payment = txn(day=18, amount="2340.00", merchant="Airtel", month=7)
    obligations, stats, _, _ = _link_bills([payment], [bill_record()])
    assert obligations[0].status != "paid"
    assert stats.bills_paid == 0


def test_a_payment_long_after_the_due_date_does_not_count():
    payment = txn(day=20, amount="2340.00", merchant="Tata Power", month=10)
    obligations, _, _, _ = _link_bills([payment], [bill_record()])
    assert obligations[0].status != "paid"


def test_a_transfer_is_never_a_bill_payment():
    payment = txn(day=18, amount="2340.00", merchant="Tata Power", month=7, is_transfer=True)
    obligations, _, _, _ = _link_bills([payment], [bill_record()])
    assert obligations[0].status != "paid"


def test_the_consumer_number_is_masked_by_the_schema():
    fields = BillFields(biller="Tata Power", amount_due=Decimal("1"),
                        consumer_no="900012345678")
    assert fields.consumer_no == "XXXXXXXX5678"


@pytest.mark.parametrize("amount", ["2339.00", "2341.00"])
def test_a_bill_payment_must_match_the_amount_exactly(amount):
    """Unlike a receipt, a bill payment is the exact amount due -- a near miss
    is a different transaction."""
    payment = txn(day=18, amount=amount, merchant="Tata Power", month=7)
    obligations, _, _, _ = _link_bills([payment], [bill_record()])
    assert obligations[0].status != "paid"
