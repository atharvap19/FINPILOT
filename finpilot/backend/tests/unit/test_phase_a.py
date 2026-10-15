"""Phase A end to end, scored against the ground truth.

This is the checkpoint-2 equivalent of the golden test: run detect -> extract ->
map -> normalise -> enrich -> validate over the tabular fixtures and compare
every field Phase A is responsible for against ``expected_transactions.csv``.

Fields that merge and link fill in (times and order detail from emails,
transfer/refund flags, the multi-source ``linked_sources``) are deliberately
excluded -- those are asserted by the golden test once phase B exists.
"""

from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.pipeline import process_tabular_document

#: The columns Phase A alone decides.
PHASE_A_FIELDS = (
    "date", "value_date", "amount", "direction", "currency", "account", "account_type",
    "source_type", "raw_description", "balance_after", "reference_no", "merchant",
    "counterparty_type", "counterparty_upi_handle", "payment_mode", "channel",
    "upi_remark", "card_last4", "emi_flag", "is_fee", "is_cash_withdrawal",
    "extraction_method", "extraction_confidence", "needs_review",
)

TABULAR_FILES = (
    "hdfc_savings_jun_aug15.csv",
    "hdfc_savings_aug.csv",
    "sbi_savings_jun_aug.csv",
    "personal_expenses.xlsx",
)

#: Sheet rows that re-describe a statement transaction. Phase A cannot know
#: they are duplicates -- link.py absorbs them in phase B.
EXPECTED_UNMATCHED_SHEET_ROWS = 3


@pytest.fixture(scope="module")
def truth(synthetic_dir: Path) -> dict[str, dict]:
    with (synthetic_dir / "expected_transactions.csv").open(encoding="utf-8") as fh:
        return {r["txn_id"]: r for r in csv.DictReader(fh)}


@pytest.fixture(scope="module")
def results(synthetic_dir: Path) -> dict:
    return {
        name: process_tabular_document(synthetic_dir / name, doc_id=f"d_{i}")
        for i, name in enumerate(TABULAR_FILES)
    }


def _as_text(value) -> str:
    return "" if value is None else str(value)


# --------------------------------------------------------------------------
# per document
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "rows", "template"),
    [
        ("hdfc_savings_jun_aug15.csv", 83, "HDFC Bank savings CSV"),
        ("hdfc_savings_aug.csv", 33, "HDFC Bank savings CSV"),
        ("sbi_savings_jun_aug.csv", 23, "State Bank of India savings CSV"),
        ("personal_expenses.xlsx", 9, None),
    ],
)
def test_every_row_is_extracted(results, name, rows, template):
    result = results[name]
    assert len(result.transactions) == rows
    if template:
        assert result.template_name == template
        assert result.template_match == "exact"


def test_statements_reconcile_exactly(results):
    for name in TABULAR_FILES[:3]:
        rec = results[name].reconciliation
        assert rec.checked is True, name
        assert rec.ok is True, f"{name}: off by {rec.difference}"
        assert rec.difference == Decimal("0.00"), name
        assert rec.running_balance_ok is True, name


def test_no_document_needs_review(results):
    for name, result in results.items():
        assert result.status == "processed", (name, [i.message for i in result.issues])
        assert result.error_count == 0, (name, [i.message for i in result.issues])


def test_the_unknown_sheet_layout_is_auto_mapped(results):
    result = results["personal_expenses.xlsx"]
    assert result.template_match == "auto"
    assert result.pending_mapping is None
    assert result.context.source_type == "spreadsheet"


# --------------------------------------------------------------------------
# against the ground truth
# --------------------------------------------------------------------------


def test_identity_matches_ground_truth(results, truth):
    """Every statement row must hash to the id the ground truth predicted."""
    produced = [t for name in TABULAR_FILES for t in results[name].transactions]
    unmatched = [t for t in produced if t.txn_id not in truth]
    assert all(t.source_ref.startswith("personal_expenses") for t in unmatched), (
        "only spreadsheet duplicates may fail to match before linking: "
        f"{[t.source_ref for t in unmatched]}"
    )
    assert len(unmatched) == EXPECTED_UNMATCHED_SHEET_ROWS


def test_field_level_accuracy_is_perfect(results, truth):
    produced = [t for name in TABULAR_FILES for t in results[name].transactions]
    wrong: Counter[str] = Counter()
    examples: dict[str, str] = {}

    compared = 0
    for txn in produced:
        expected = truth.get(txn.txn_id)
        if expected is None:
            continue
        compared += 1
        dumped = txn.model_dump(mode="json")
        for column in PHASE_A_FIELDS:
            got, want = _as_text(dumped.get(column)), _as_text(expected.get(column))
            if column == "extraction_confidence":
                got, want = f"{float(got):.2f}", f"{float(want or 0):.2f}"
            if got != want:
                wrong[column] += 1
                examples.setdefault(column, f"{txn.source_ref}: got {got!r} want {want!r}")

    assert compared >= 130
    assert not wrong, "\n".join(f"{c} x{n}: {examples[c]}" for c, n in wrong.most_common())


def test_the_overlapping_statements_produce_identical_ids(results):
    """The August rows appear in both HDFC files and must hash the same, which
    is what lets merge collapse them in phase B."""
    a = {t.txn_id for t in results["hdfc_savings_jun_aug15.csv"].transactions}
    b = {t.txn_id for t in results["hdfc_savings_aug.csv"].transactions}
    assert len(a & b) == 19


def test_reprocessing_a_file_is_a_no_op(synthetic_dir: Path):
    """Principle 5: the same file twice produces the same ids."""
    first = process_tabular_document(synthetic_dir / TABULAR_FILES[0], doc_id="d_a")
    second = process_tabular_document(synthetic_dir / TABULAR_FILES[0], doc_id="d_b")
    assert [t.txn_id for t in first.transactions] == [t.txn_id for t in second.transactions]


# --------------------------------------------------------------------------
# privacy
# --------------------------------------------------------------------------


SECRETS = ("50100234567890", "20012345678901", "4477123456784417",
           "ramesh.kumar@", "swiggy.payu@", "9820045612@", "8765432109@")


def test_nothing_unmasked_reaches_the_ledger(results):
    for name, result in results.items():
        for txn in result.transactions:
            blob = " ".join(_as_text(v) for v in txn.model_dump(mode="json").values())
            for secret in SECRETS:
                assert secret not in blob, f"{secret} leaked into {txn.source_ref} ({name})"


def test_nothing_unmasked_reaches_staging(results):
    """Staging is previewed in the mapping dialog, so it is masked too."""
    for name, result in results.items():
        for row in result.staging:
            blob = " ".join(_as_text(v) for v in row["payload"].values())
            for secret in SECRETS:
                assert secret not in blob, f"{secret} leaked into staging of {name}"


def test_staging_keeps_a_row_per_extracted_row(results):
    for name in TABULAR_FILES:
        result = results[name]
        assert len(result.staging) == len(result.transactions)
        assert all("#" in row["source_ref"] for row in result.staging)
