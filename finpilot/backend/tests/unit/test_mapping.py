"""Column mapping: fingerprints, templates, and the proposal ladder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.mapping import (
    AUTO_ACCEPT,
    Template,
    TemplateLibrary,
    header_fingerprint,
    infer_amount_style,
    loose_fingerprint,
    normalize_header,
    propose_mapping,
    template_from_mapping,
)

HDFC_HEADERS = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.",
                "Deposit Amt.", "Closing Balance"]
SBI_HEADERS = ["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit",
               "Credit", "Balance"]
CARD_HEADERS = ["Date", "Transaction Description", "MCC", "Amount (in Rs.)"]


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Withdrawal Amt. ", "withdrawal amt"),
        ("Chq./Ref.No.", "chq ref no"),
        ("Amount (in Rs.)", "amount in rs"),
        ("", ""),
    ],
)
def test_normalize_header(raw, expected):
    assert normalize_header(raw) == expected


def test_fingerprint_is_order_sensitive_and_loose_is_not():
    reordered = [HDFC_HEADERS[1], HDFC_HEADERS[0], *HDFC_HEADERS[2:]]
    assert header_fingerprint(HDFC_HEADERS) != header_fingerprint(reordered)
    assert loose_fingerprint(HDFC_HEADERS) == loose_fingerprint(reordered)


def test_loose_fingerprint_ignores_a_blank_trailing_column():
    assert loose_fingerprint(HDFC_HEADERS) == loose_fingerprint([*HDFC_HEADERS, ""])


def test_fingerprint_survives_punctuation_drift():
    tweaked = ["Date", "Narration", "Chq / Ref No", "Value Dt", "Withdrawal Amt",
               "Deposit Amt", "Closing Balance"]
    assert header_fingerprint(tweaked) == header_fingerprint(HDFC_HEADERS)


# --------------------------------------------------------------------------
# the shipped templates
# --------------------------------------------------------------------------


def test_shipped_templates_load():
    library = TemplateLibrary()
    names = {t.name for t in library.templates}
    assert "HDFC Bank savings CSV" in names
    assert "State Bank of India savings CSV" in names
    assert "HDFC Bank credit card statement PDF" in names


@pytest.mark.parametrize(
    ("headers", "name", "style"),
    [
        (HDFC_HEADERS, "HDFC Bank savings CSV", "debit_credit"),
        (SBI_HEADERS, "State Bank of India savings CSV", "debit_credit"),
        (CARD_HEADERS, "HDFC Bank credit card statement PDF", "dr_cr_suffix"),
    ],
)
def test_templates_match_their_layouts(headers, name, style):
    template, how = TemplateLibrary().find(headers)
    assert template is not None
    assert template.name == name
    assert template.amount_style == style
    assert how == "exact"


def test_reordered_columns_still_match_loosely():
    template, how = TemplateLibrary().find([SBI_HEADERS[2], SBI_HEADERS[0], *SBI_HEADERS[1:2],
                                            *SBI_HEADERS[3:]])
    assert template is not None
    assert how == "loose"


def test_unknown_layout_matches_nothing():
    template, how = TemplateLibrary().find(["Foo", "Bar", "Baz"])
    assert template is None
    assert how == "none"


def test_every_template_maps_the_required_fields():
    for template in TemplateLibrary().templates:
        assert "date" in template.columns, template.name
        assert "description" in template.columns, template.name
        assert set(template.columns) & {"debit", "credit", "amount"}, template.name
        # every mapped header must exist in the fingerprinted header list
        for header in template.columns.values():
            assert header in template.header_tokens, f"{template.name}: {header}"


# --------------------------------------------------------------------------
# proposing a mapping
# --------------------------------------------------------------------------


def test_proposes_a_usable_mapping_for_a_plain_sheet():
    headers = ["Date", "Description", "Amount", "Mode", "Note"]
    rows = [["07-06-2025", "Chai and vada pav", "-120", "Cash", "office"]]
    proposal = propose_mapping(headers, rows)
    assert proposal.is_usable
    assert proposal.columns["date"] == "Date"
    assert proposal.columns["description"] == "Description"
    assert proposal.columns["amount"] == "Amount"
    assert proposal.amount_style == "signed"
    assert proposal.uncertain == []


def test_proposal_recognises_separate_debit_credit_columns():
    proposal = propose_mapping(HDFC_HEADERS, [])
    assert proposal.amount_style == "debit_credit"
    assert proposal.columns["debit"] == "Withdrawal Amt."
    assert proposal.columns["credit"] == "Deposit Amt."
    assert proposal.columns["balance_after"] == "Closing Balance"


def test_proposal_recognises_a_dr_cr_suffix():
    rows = [["21/06/2025", "SWIGGY BANGALORE IN", "5812", "432.00 Dr"],
            ["02/07/2025", "PAYMENT RECEIVED", "", "18,450.75 Cr"]]
    proposal = propose_mapping(CARD_HEADERS, rows)
    assert proposal.amount_style == "dr_cr_suffix"
    assert proposal.columns["amount"] == "Amount (in Rs.)"


def test_proposal_recognises_a_dr_cr_flag_column():
    headers = ["Txn Date", "Particulars", "Amount", "Dr/Cr", "Balance"]
    proposal = propose_mapping(headers, [["01/06/2025", "X", "100.00", "Dr", "900.00"]])
    assert proposal.amount_style == "dr_cr_flag"


def test_one_column_never_maps_to_two_fields():
    proposal = propose_mapping(HDFC_HEADERS, [])
    headers_used = [p.header for p in proposal.proposals if p.field != "ignore"]
    assert len(headers_used) == len(set(headers_used))


def test_unrecognisable_headers_are_flagged_for_the_user():
    """The whole point of `uncertain` is that it routes to the mapping dialog
    rather than guessing."""
    proposal = propose_mapping(["Col1", "Col2", "Col3"], [["a", "b", "c"]])
    assert proposal.uncertain
    assert not proposal.is_usable


def test_an_extra_column_we_would_drop_does_not_block_the_upload():
    """An export with an invoice-id column (mostly blank) still maps itself."""
    headers = ["transaction_id", "date", "description", "category", "type", "amount",
               "currency", "payment_method", "merchant", "bill_id", "running_balance"]
    row = ["TXN-1", "2025-01-01", "Card purchase — Metro", "Transport", "Debit", "-208.45",
           "USD", "Credit card", "Metro", "INV-2025-00004", "2241.55"]
    proposal = propose_mapping(headers, [row])
    assert proposal.is_usable
    assert "bill_id" not in proposal.columns.values()
    assert proposal.amount_style == "dr_cr_flag"


def test_an_empty_alias_never_claims_a_column():
    """The "#" alias normalises to nothing; it must not prefix-match every header."""
    proposal = propose_mapping(["Date", "Narration", "Amount", "Zzz"], [["01/06/2025", "x", "1", "q"]])
    zzz = next(p for p in proposal.proposals if p.header == "Zzz")
    assert "'#'" not in zzz.reason


def test_a_mapping_missing_an_amount_is_not_usable():
    proposal = propose_mapping(["Date", "Narration"], [["01/06/2025", "something"]])
    assert not proposal.is_usable


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"debit", "credit"}, "debit_credit"),
        ({"amount", "dr_cr_flag"}, "dr_cr_flag"),
        ({"amount"}, "signed"),
    ],
)
def test_infer_amount_style(fields, expected):
    assert infer_amount_style(fields, [], []) == expected


def test_auto_accept_threshold_is_respected():
    proposal = propose_mapping(HDFC_HEADERS, [])
    for p in proposal.proposals:
        if p.header in ("Date", "Narration"):
            assert p.confidence >= AUTO_ACCEPT


# --------------------------------------------------------------------------
# learning a template
# --------------------------------------------------------------------------


def test_a_confirmed_mapping_becomes_a_reusable_template(tmp_path: Path):
    headers = ["Trans Date", "Remarks", "Withdrawal", "Deposit", "Running Balance"]
    template = template_from_mapping(
        name="Tiny Bank CSV",
        headers=headers,
        columns={"date": "Trans Date", "description": "Remarks", "debit": "Withdrawal",
                 "credit": "Deposit", "balance_after": "Running Balance"},
        amount_style="debit_credit",
        issuer="Tiny Bank",
    )
    library = TemplateLibrary(tmp_path)
    assert library.find(headers)[0] is None

    path = library.save(template)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["issuer"] == "Tiny Bank"

    found, how = library.find(headers)
    assert found is not None and how == "exact"
    # and it survives a fresh library reading the same directory
    assert TemplateLibrary(tmp_path).find(headers)[0] is not None


def test_template_round_trips_through_json():
    original = TemplateLibrary().templates[0]
    assert Template.from_json(original.to_json()).columns == original.columns
