"""The contract: identity, validation, and the shape of the exported frame."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ingestion.schema import (
    ALL_TRANSACTION_COLUMNS,
    METHOD_CONFIDENCE,
    RESERVED_COLUMNS,
    TRANSACTION_COLUMNS,
    Direction,
    Obligation,
    Transaction,
    empty_transaction_frame,
    make_txn_id,
    quantize_money,
    transactions_to_frame,
    validate_transaction_frame,
)


def _txn(**overrides) -> Transaction:
    base = dict(
        txn_id="t_test",
        date=dt.date(2025, 6, 5),
        amount=Decimal("22000.00"),
        direction="debit",
        account="HDFC-XX7890",
        account_type="bank",
        source_type="bank_statement",
        source_ref="hdfc.csv#6",
        raw_description="UPI-RAMESH KUMAR-XXXX@OKAXIS-UTIB0000123-500000000002-RENT JUNE",
        extraction_method="template",
    )
    base.update(overrides)
    return Transaction(**base)


# --------------------------------------------------------------------------
# txn_id
# --------------------------------------------------------------------------


def test_txn_id_is_stable():
    args = dict(
        account="HDFC-XX7890",
        date=dt.date(2025, 6, 5),
        amount=Decimal("22000.00"),
        direction="debit",
        reference_no="500000000002",
    )
    assert make_txn_id(**args) == make_txn_id(**args)


def test_txn_id_ignores_source_so_reuploads_collide():
    """The same payment read from two overlapping statements is one row."""
    args = dict(
        account="HDFC-XX7890",
        date=dt.date(2025, 8, 5),
        amount=Decimal("22000.00"),
        direction="debit",
        reference_no="500000000003",
    )
    from_file_a = make_txn_id(**args, raw_description="narration as printed in file A")
    from_file_b = make_txn_id(**args, raw_description="narration as printed in file B")
    # reference wins over narration, so the two files agree
    assert from_file_a == from_file_b


def test_txn_id_falls_back_to_narration_without_a_reference():
    base = dict(
        account="HDFC-XX7890",
        date=dt.date(2025, 6, 18),
        amount=Decimal("5000.00"),
        direction="debit",
    )
    a = make_txn_id(**base, raw_description="NWD-XXXXXXXXXXXX4417-HDFC BANK ATM")
    b = make_txn_id(**base, raw_description="nwd-xxxxxxxxxxxx4417-hdfc  bank atm")
    assert a == b, "narration hashing is case- and whitespace-insensitive"
    c = make_txn_id(**base, raw_description="something else entirely")
    assert a != c


def test_txn_id_distinguishes_direction_and_occurrence():
    base = dict(
        account="HDFC-XX7890",
        date=dt.date(2025, 6, 5),
        amount=Decimal("100.00"),
        raw_description="UPI-SHOP",
    )
    assert make_txn_id(**base, direction="debit") != make_txn_id(**base, direction="credit")
    assert make_txn_id(**base, direction="debit") != make_txn_id(
        **base, direction="debit", occurrence=1
    )


# --------------------------------------------------------------------------
# Transaction model
# --------------------------------------------------------------------------


def test_amount_is_always_positive():
    with pytest.raises(ValidationError):
        _txn(amount=Decimal("-1.00"))


def test_money_is_quantized_to_paise():
    assert _txn(amount=Decimal("491.4949")).amount == Decimal("491.49")
    assert quantize_money("1.005") == Decimal("1.01")


def test_signed_amount_helper():
    assert _txn(direction="debit").signed_amount == Decimal("-22000.00")
    assert _txn(direction="credit").signed_amount == Decimal("22000.00")


def test_card_last4_must_be_four_digits():
    assert _txn(card_last4="4417").card_last4 == "4417"
    assert _txn(card_last4="").card_last4 is None
    with pytest.raises(ValidationError):
        _txn(card_last4="44177")


def test_upi_handle_is_reduced_to_the_domain():
    """Storing a full VPA would be a privacy leak; only the handle survives."""
    assert _txn(counterparty_upi_handle="@ybl").counterparty_upi_handle == "@ybl"
    assert _txn(counterparty_upi_handle="ramesh@okaxis").counterparty_upi_handle == "@okaxis"


def test_balance_after_may_be_negative():
    assert _txn(balance_after=Decimal("-1200.00")).balance_after == Decimal("-1200.00")


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        _txn(segment_confidence=0.5)


def test_reserved_columns_default_to_empty():
    txn = _txn()
    for column in RESERVED_COLUMNS:
        assert getattr(txn, column) is None, f"{column} is owned by a later stage"


def test_enum_values_serialise_as_plain_strings():
    dumped = _txn().model_dump(mode="json")
    assert dumped["direction"] == Direction.DEBIT.value
    assert dumped["amount"] == "22000.00"


# --------------------------------------------------------------------------
# frame helpers
# --------------------------------------------------------------------------


def test_column_order_is_the_contract():
    assert ALL_TRANSACTION_COLUMNS == TRANSACTION_COLUMNS + RESERVED_COLUMNS
    assert len(set(ALL_TRANSACTION_COLUMNS)) == len(ALL_TRANSACTION_COLUMNS)
    assert ALL_TRANSACTION_COLUMNS[0] == "txn_id"


def test_empty_frame_has_every_column():
    df = empty_transaction_frame()
    assert list(df.columns) == list(ALL_TRANSACTION_COLUMNS)
    assert df.empty


def test_frame_round_trip_and_validation():
    rows = [
        _txn(txn_id="t_a"),
        _txn(txn_id="t_b", direction="credit", amount=Decimal("125000.00")),
    ]
    df = transactions_to_frame(rows)
    assert list(df.columns) == list(ALL_TRANSACTION_COLUMNS)
    assert df["amount"].tolist() == [22000.0, 125000.0]
    assert df["needs_review"].dtype == bool
    _, errors = validate_transaction_frame(df)
    assert errors == []


def test_frame_validation_reports_rather_than_raises():
    df = transactions_to_frame([_txn(txn_id="t_a"), _txn(txn_id="t_a")])
    _, errors = validate_transaction_frame(df)
    assert errors, "duplicate txn_id must be reported"
    assert any("txn_id" in e for e in errors)


def test_source_ref_must_be_traceable():
    """Principle 4: a row that cannot be traced back to a file+row is a bug."""
    df = transactions_to_frame([_txn(source_ref="no-hash-here")])
    _, errors = validate_transaction_frame(df)
    assert any("source_ref" in e for e in errors)


def test_method_confidence_covers_every_method():
    from ingestion.schema import ExtractionMethod

    assert set(METHOD_CONFIDENCE) == {m.value for m in ExtractionMethod}
    assert all(0.0 < v <= 1.0 for v in METHOD_CONFIDENCE.values())


# --------------------------------------------------------------------------
# obligations
# --------------------------------------------------------------------------


def test_obligation_defaults_to_due():
    bill = Obligation(
        bill_id="b_1",
        biller="Tata Power",
        amount_due=Decimal("2340.00"),
        source_ref="bills/x.png#1:1",
    )
    assert bill.status == "due"
    assert bill.paid_txn_id is None
    assert bill.model_dump(mode="json")["amount_due"] == "2340.00"
