"""Embeddings, and what happens when they are not installed.

``sentence-transformers`` is an optional extra, so the important behaviour to
pin down is the *degradation*: with the dependency missing every function here
must say "no opinion" and let the caller fall through to asking the user.
Silently returning "no match" instead would stop the escalation and quietly
mis-map a column.

The semantic tests are skipped without the extra installed.
"""

from __future__ import annotations

import pytest

from ingestion import embeddings as emb
from ingestion.mapping import propose_mapping, refine_proposal

needs_model = pytest.mark.skipif(
    not emb.available(), reason="install the 'embeddings' extra to run this"
)
needs_no_model = pytest.mark.skipif(
    emb.available(), reason="only meaningful without the 'embeddings' extra"
)


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


def test_available_is_honest_about_the_optional_dependency():
    assert isinstance(emb.available(), bool)


@needs_no_model
def test_everything_returns_no_opinion_without_the_extra():
    assert emb.encode(["anything"]) is None
    assert emb.rank("Particulars", ["a", "b"]) == []
    assert emb.map_header("Particulars") is None
    assert emb.map_headers(["Particulars", "Withdrawal"]) == {}
    assert emb.best("x", ["y"], threshold=0.5) is None


@needs_no_model
def test_the_merchant_index_reports_that_it_is_not_ready():
    index = emb.MerchantEmbeddingIndex()
    assert index.build(["Swiggy", "Zomato"]) is False
    assert index.ready is False
    assert index.best("Swiggy Instamart") is None


def test_empty_input_never_loads_the_model():
    assert emb.encode([]) is None
    assert emb.rank("", ["a"]) == []
    assert emb.rank("a", []) == []


def test_refine_is_a_no_op_when_nothing_is_uncertain():
    proposal = propose_mapping(
        ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"], []
    )
    assert proposal.uncertain == []
    assert refine_proposal(proposal, [], []) is proposal


def test_refine_without_embeddings_leaves_it_uncertain():
    """The column still goes to the user, which is the point: nothing else is
    going to resolve it, and guessing silently is how a statement ends up
    wrong in a way nobody notices."""
    proposal = propose_mapping(["Col1", "Col2", "Col3"], [["a", "b", "c"]])
    assert proposal.uncertain
    refined = refine_proposal(proposal, ["Col1", "Col2", "Col3"], [["a", "b", "c"]])
    assert not refined.is_usable


# --------------------------------------------------------------------------
# semantics, when the extra is installed
# --------------------------------------------------------------------------


@needs_model
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Particulars", "description"),
        ("Transaction Remarks", "description"),
        ("Withdrawal", "debit"),
        ("Running Balance", "balance_after"),
    ],
)
def test_headers_map_by_meaning_not_by_spelling(header, expected):
    result = emb.map_header(header, threshold=0.4)
    assert result is not None and result[0] == expected


@needs_model
def test_one_field_is_not_claimed_twice():
    mapped = emb.map_headers(["Particulars", "Transaction Remarks"], threshold=0.4)
    assert len(set(name for name, _ in mapped.values())) == len(mapped)


@needs_model
def test_merchant_index_matches_a_variant_spelling():
    index = emb.MerchantEmbeddingIndex()
    assert index.build(["Swiggy", "Zomato", "BigBasket", "HPCL"])
    hit = index.best("Swiggy Instamart", threshold=0.5)
    assert hit is not None and hit.candidate == "Swiggy"


@needs_model
def test_an_unrelated_name_is_rejected_by_the_threshold():
    index = emb.MerchantEmbeddingIndex()
    index.build(["Swiggy", "Zomato"])
    assert index.best("Municipal Water Board", threshold=0.9) is None


# --------------------------------------------------------------------------
# merchant refinement
# --------------------------------------------------------------------------


def test_only_unresolved_names_reach_the_expensive_tier():
    """Swiggy is in the dictionary; the corner shop is not."""
    from ingestion.merchants import MerchantResolver

    resolver = MerchantResolver()
    leftovers = resolver.unresolved(["Swiggy", "Zomato", "Sharma Kirana Bhandar"])
    assert leftovers == ["Sharma Kirana Bhandar"]


def test_refine_without_embeddings_leaves_the_cleaned_name():
    """No model is called, so an unknown shop keeps its tidied-up name rather
    than being guessed at."""
    from ingestion.merchants import MerchantResolver

    resolver = MerchantResolver()
    resolver.refine(["Sharma Kirana Bhandar Pvt Ltd"])
    match = resolver.resolve("Sharma Kirana Bhandar Pvt Ltd")
    assert match.canonical == "Sharma Kirana Bhandar"
    assert match.method == "cleaned"


def test_a_learned_name_is_reused():
    from ingestion.merchants import MerchantResolver

    resolver = MerchantResolver()
    resolver.learn("Sharma Kirana", ("sharma kirana bhandar",))
    assert resolver.resolve("Sharma Kirana Bhandar").canonical == "Sharma Kirana"
    assert resolver.is_known("Sharma Kirana Bhandar")
