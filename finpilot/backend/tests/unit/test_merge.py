"""Merging: one row per transaction, whatever described it."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.merge import (
    LinkRegistry,
    SourceRow,
    document_sort_key,
    merge_documents,
    merge_transactions,
)
from ingestion.schema import Transaction, make_txn_id


def txn(
    *,
    day: int = 5,
    amount: str = "22000.00",
    direction: str = "debit",
    source_type: str = "bank_statement",
    source_ref: str = "a.csv#1",
    reference_no: str | None = "500000000002",
    balance: str | None = "900.00",
    **kw,
) -> Transaction:
    date = dt.date(2025, 6, day)
    payload = dict(
        txn_id=make_txn_id(
            account="HDFC-XX7890", date=date, amount=Decimal(amount),
            direction=direction, reference_no=reference_no,
        ),
        date=date,
        amount=Decimal(amount),
        direction=direction,
        account="HDFC-XX7890",
        account_type="bank",
        source_type=source_type,
        source_ref=source_ref,
        reference_no=reference_no,
        raw_description="UPI-RAMESH KUMAR-XXXX@OKAXIS-UTIB0000123-500000000002-RENT JUNE",
        balance_after=Decimal(balance) if balance else None,
        extraction_method="template",
        linked_sources=source_ref,
    )
    payload.update(kw)
    return Transaction(**payload)


def src(transaction: Transaction, order: int = 0, doc_id: str = "d_1") -> SourceRow:
    return SourceRow(
        transaction=transaction, doc_id=doc_id, doc_order=order,
        filename=transaction.source_ref.split("#")[0],
    )


# --------------------------------------------------------------------------
# document ordering
# --------------------------------------------------------------------------


def test_the_earlier_statement_sorts_first():
    a = document_sort_key(statement_start=dt.date(2025, 6, 1),
                          statement_end=dt.date(2025, 8, 15), filename="a.csv")
    b = document_sort_key(statement_start=dt.date(2025, 8, 1),
                          statement_end=dt.date(2025, 8, 31), filename="b.csv")
    assert a < b


def test_documents_without_a_period_sort_last():
    """A spreadsheet must not take the primary slot from a statement."""
    statement = document_sort_key(statement_start=dt.date(2025, 6, 1),
                                  statement_end=dt.date(2025, 8, 15), filename="a.csv")
    sheet = document_sort_key(statement_start=None, statement_end=None, filename="a.xlsx")
    assert statement < sheet


# --------------------------------------------------------------------------
# collapsing duplicates
# --------------------------------------------------------------------------


def test_the_same_row_in_two_statements_becomes_one():
    a = txn(source_ref="jun_aug.csv#66")
    b = txn(source_ref="aug.csv#2")
    result = merge_transactions([src(a, 0), src(b, 1)])
    assert len(result.transactions) == 1
    assert result.duplicates_merged == 1


def test_the_earlier_document_wins_the_primary_slot():
    a = txn(source_ref="jun_aug.csv#66")
    b = txn(source_ref="aug.csv#2")
    # deliberately fed in the wrong order
    result = merge_transactions([src(b, 1), src(a, 0)])
    assert result.transactions[0].source_ref == "jun_aug.csv#66"


def test_both_sources_are_recorded_in_linked_sources():
    result = merge_transactions(
        [src(txn(source_ref="jun_aug.csv#66"), 0), src(txn(source_ref="aug.csv#2"), 1)]
    )
    assert result.transactions[0].linked_sources == "aug.csv#2|jun_aug.csv#66"
    assert len(result.links) == 2
    assert {link.link_type for link in result.links} == {"primary"}


def test_a_statement_outranks_a_spreadsheet_describing_the_same_row():
    statement = txn(source_ref="stmt.csv#6")
    sheet = txn(source_type="spreadsheet", source_ref="sheet.xlsx#5",
                extraction_method="spreadsheet", balance=None)
    result = merge_transactions([src(sheet, 0), src(statement, 1)])
    kept = result.transactions[0]
    assert kept.source_type == "bank_statement"
    assert kept.source_ref == "stmt.csv#6"
    assert kept.linked_sources == "stmt.csv#6|sheet.xlsx#5"


def test_link_types_follow_the_source():
    statement = txn(source_ref="stmt.csv#6")
    sheet = txn(source_type="spreadsheet", source_ref="sheet.xlsx#5",
                extraction_method="spreadsheet", balance=None)
    result = merge_transactions([src(statement, 0), src(sheet, 1)])
    by_ref = {link.source_ref: link.link_type for link in result.links}
    assert by_ref["stmt.csv#6"] == "primary"
    assert by_ref["sheet.xlsx#5"] == "spreadsheet"


def test_distinct_transactions_are_not_merged():
    a = txn(reference_no="500000000001", source_ref="a.csv#1")
    b = txn(reference_no="500000000002", source_ref="a.csv#2", balance="800.00")
    result = merge_transactions([src(a), src(b)])
    assert len(result.transactions) == 2
    assert result.duplicates_merged == 0


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------


def test_disagreeing_balances_are_flagged_not_silently_collapsed():
    """Same date, amount and narration but a different running balance means
    these are probably two separate transactions."""
    a = txn(source_ref="a.csv#1", balance="900.00")
    b = txn(source_ref="b.csv#1", balance="700.00")
    result = merge_transactions([src(a, 0), src(b, 1)])
    assert result.conflicts
    assert result.conflicts[0]["field"] == "balance_after"
    assert result.transactions[0].needs_review is True
    assert any(i.code == "duplicate_balance_mismatch" for i in result.issues)


def test_identical_balances_raise_no_conflict():
    result = merge_transactions(
        [src(txn(source_ref="a.csv#1"), 0), src(txn(source_ref="b.csv#1"), 1)]
    )
    assert result.conflicts == []
    assert result.transactions[0].needs_review is False


def test_a_lower_precedence_source_disagreeing_is_a_warning_not_an_overwrite():
    statement = txn(source_ref="stmt.csv#6", amount="22000.00")
    sheet = txn(source_type="spreadsheet", source_ref="sheet.xlsx#5",
                extraction_method="spreadsheet", balance=None,
                raw_description="Rent July")
    result = merge_transactions([src(statement, 0), src(sheet, 1)])
    kept = result.transactions[0]
    assert "RAMESH" in kept.raw_description, "the statement's narration is kept"
    assert any(c["field"] == "raw_description" for c in result.conflicts)
    assert any(i.code == "source_conflict" and i.severity == "warning" for i in result.issues)


# --------------------------------------------------------------------------
# enrichment
# --------------------------------------------------------------------------


def test_a_lower_precedence_source_may_fill_a_blank():
    statement = txn(source_ref="stmt.csv#6", merchant=None, time=None)
    email = txn(source_type="email", source_ref="mail.eml#1", extraction_method="email",
                balance=None, merchant="Myntra", time=dt.time(21, 6),
                order_id="MYN-1180042317")
    result = merge_transactions([src(statement, 0), src(email, 1)])
    kept = result.transactions[0]
    assert kept.merchant == "Myntra"
    assert kept.time == dt.time(21, 6)
    assert kept.order_id == "MYN-1180042317"
    assert result.enriched_fields >= 3


def test_filling_a_blank_never_overwrites_a_value():
    statement = txn(source_ref="stmt.csv#6", merchant="Myntra")
    email = txn(source_type="email", source_ref="mail.eml#1", extraction_method="email",
                balance=None, merchant="Myntra Designs Private Limited")
    result = merge_transactions([src(statement, 0), src(email, 1)])
    assert result.transactions[0].merchant == "Myntra"


def test_flags_are_or_ed_across_sources():
    statement = txn(source_ref="stmt.csv#6")
    email = txn(source_type="email", source_ref="mail.eml#1", extraction_method="email",
                balance=None, is_refund=True)
    result = merge_transactions([src(statement, 0), src(email, 1)])
    assert result.transactions[0].is_refund is True


# --------------------------------------------------------------------------
# on the real fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def merged(synthetic_dir: Path):
    from ingestion.pipeline import process_pdf_document, process_tabular_document

    docs = [
        process_tabular_document(synthetic_dir / name, doc_id=f"d_{i}")
        for i, name in enumerate(
            ["hdfc_savings_jun_aug15.csv", "hdfc_savings_aug.csv",
             "sbi_savings_jun_aug.csv", "personal_expenses.xlsx"]
        )
    ]
    docs.append(process_pdf_document(synthetic_dir / "hdfc_card_jul2025.pdf", doc_id="d_card"))
    return docs, merge_documents(docs)


def test_the_overlap_is_collapsed_exactly(merged):
    docs, result = merged
    before = sum(len(d.transactions) for d in docs)
    assert before == 158
    assert len(result.transactions) == 139
    assert result.duplicates_merged == 19


def test_merging_the_real_files_produces_no_conflicts(merged):
    _, result = merged
    assert result.conflicts == []
    assert [i for i in result.issues if i.severity == "error"] == []


def test_the_overlap_keeps_the_earlier_statement_as_primary(merged):
    _, result = merged
    both = [
        t for t in result.transactions
        if "hdfc_savings_aug.csv" in t.linked_sources
        and "hdfc_savings_jun_aug15.csv" in t.linked_sources
    ]
    assert len(both) == 19
    for txn_row in both:
        assert txn_row.source_ref.startswith("hdfc_savings_jun_aug15.csv")


def test_every_row_still_traces_to_its_primary_source(merged):
    _, result = merged
    for t in result.transactions:
        assert t.source_ref in t.linked_sources


def test_merging_is_idempotent(merged):
    """Re-running phase B over its own output changes nothing (principle 5)."""
    _, result = merged
    again = merge_transactions(
        [src(t, 0, "d_x") for t in result.transactions]
    )
    assert [t.txn_id for t in again.transactions] == [t.txn_id for t in result.transactions]
    assert again.duplicates_merged == 0


# --------------------------------------------------------------------------
# the link registry
# --------------------------------------------------------------------------


def test_registry_renders_linked_sources_in_rank_order():
    row = txn(source_ref="stmt.csv#6")
    registry = LinkRegistry()
    registry.add(row, doc_id="d_1", source_ref="stmt.csv#6", link_type="primary")
    registry.add(row, doc_id="d_3", source_ref="sheet.xlsx#5", link_type="spreadsheet")
    registry.add(row, doc_id="d_2", source_ref="mail.eml#1", link_type="email")
    assert row.linked_sources == "stmt.csv#6|mail.eml#1|sheet.xlsx#5"


def test_registry_ignores_a_repeated_link():
    row = txn(source_ref="stmt.csv#6")
    registry = LinkRegistry()
    assert registry.add(row, doc_id="d_1", source_ref="mail.eml#1", link_type="email") is not None
    assert registry.add(row, doc_id="d_1", source_ref="mail.eml#1", link_type="email") is None
    assert len(registry) == 1


def test_registry_is_seeded_from_a_merge_result():
    a = txn(source_ref="jun_aug.csv#66")
    b = txn(source_ref="aug.csv#2")
    result = merge_transactions([src(a, 0), src(b, 1)])
    registry = LinkRegistry(result)
    assert len(registry.for_txn(a.txn_id)) == 2
    registry.add(result.transactions[0], doc_id="d_m", source_ref="mail.eml#1",
                 link_type="email")
    assert result.transactions[0].linked_sources.endswith("mail.eml#1")
