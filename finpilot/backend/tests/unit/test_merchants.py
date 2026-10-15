"""Merchant resolution: cleaning, the dictionary, and what must not happen."""

from __future__ import annotations

import pytest

from ingestion.merchants import (
    MerchantResolver,
    clean_merchant_name,
    default_resolver,
    smart_title,
)


@pytest.fixture()
def resolver() -> MerchantResolver:
    return MerchantResolver()


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("POS 447712XXXXXX4417 RELIANCE RETAIL", "RELIANCE RETAIL"),
        ("swiggy.payu@hdfcbank", "swiggy"),
        ("myntra.rzp@hdfcbank", "myntra"),
        ("ACME TECHNOLOGIES PVT LTD", "ACME TECHNOLOGIES"),
        ("ZOMATO LTD", "ZOMATO"),
        ("UPI-BIGBASKET", "BIGBASKET"),
        ("SOME SHOP 123456", "SOME SHOP"),
    ],
)
def test_clean_merchant_name(raw, expected):
    assert clean_merchant_name(raw) == expected


def test_cleaning_never_empties_a_name():
    """``India Ltd`` is all suffix -- stripping everything would lose the row."""
    assert clean_merchant_name("India Ltd")


def test_legal_forms_are_stripped_but_real_words_are_not():
    """`Technologies` is part of the company name, `Pvt Ltd` is not."""
    assert clean_merchant_name("ACME TECHNOLOGIES PVT LTD") == "ACME TECHNOLOGIES"
    assert "SERVICES" in clean_merchant_name("AMAZON WEB SERVICES")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HDFC BANK", "HDFC Bank"),
        ("STATE BANK OF INDIA", "State Bank of India"),
        ("ACME TECHNOLOGIES", "Acme Technologies"),
        ("RAMESH KUMAR", "Ramesh Kumar"),
        ("HPCL", "HPCL"),
    ],
)
def test_smart_title_leaves_acronyms_alone(raw, expected):
    assert smart_title(raw) == expected


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SWIGGY", "Swiggy"),
        ("ZOMATO LTD", "Zomato"),
        ("BIGBASKET", "BigBasket"),
        ("DMART AVENUE SUPERMART", "DMart"),
        ("HPCL PETROL PUMP", "HPCL"),
        ("NETFLIX ENTERTAINMENT SER", "Netflix"),
        ("SPOTIFY INDIA", "Spotify"),
        ("CULT FIT", "Cult.fit"),
        ("AMAZON IN", "Amazon"),
        ("MYNTRA DESIGNS", "Myntra"),
        ("UBER INDIA", "Uber"),
        ("SAMSUNG INDIA ELECTRONICS", "Samsung"),
        ("TATA POWER", "Tata Power"),
        ("AIRTEL POSTPAID", "Airtel"),
        ("Amazon In", "Amazon"),
    ],
)
def test_known_merchants_resolve_to_canonical_spelling(resolver, raw, expected):
    assert resolver.resolve(raw).canonical == expected


def test_canonical_casing_comes_from_the_dictionary_not_title_case(resolver):
    """``BigBasket`` and ``Cult.fit`` are how the brands are written; plain
    title-casing would produce ``Bigbasket`` and ``Cult Fit``."""
    assert resolver.resolve("bigbasket").canonical == "BigBasket"
    assert resolver.resolve("cultfit").canonical == "Cult.fit"


def test_a_more_specific_name_is_not_collapsed_into_a_shorter_one(resolver):
    """AWS must not become Amazon."""
    assert resolver.resolve("AMAZON WEB SERVICES").canonical == "Amazon Web Services"
    assert resolver.resolve("AMAZON IN").canonical == "Amazon"


def test_a_short_alias_never_captures_an_unrelated_name(resolver):
    """``vi`` is a real alias for Vodafone Idea and would otherwise fuzzy-match
    half the names in the country."""
    assert resolver.resolve("Vikram Singh").canonical != "Vodafone Idea"


def test_people_skip_the_dictionary_entirely(resolver):
    for name in ("Vikram Singh", "Ramesh Kumar", "Priya Nair"):
        match = resolver.resolve(name.upper(), is_person=True)
        assert match.canonical == name
        assert match.method == "person"


def test_an_unknown_merchant_still_gets_a_usable_name(resolver):
    match = resolver.resolve("ACME TECHNOLOGIES PVT LTD")
    assert match.canonical == "Acme Technologies"
    assert match.method == "cleaned"
    assert match.confidence < 0.9, "a guess must not claim to be a dictionary hit"


def test_resolution_from_a_vpa_when_the_name_is_missing(resolver):
    assert resolver.resolve(None, vpa="swiggy.payu@hdfcbank").canonical == "Swiggy"


def test_nothing_in_nothing_out(resolver):
    match = resolver.resolve(None)
    assert match.canonical is None
    assert match.method == "none"


# --------------------------------------------------------------------------
# is_known, which narration parsing depends on
# --------------------------------------------------------------------------


def test_is_known_separates_shops_from_people(resolver):
    assert resolver.is_known("Amazon In") is True
    assert resolver.is_known("SWIGGY") is True
    assert resolver.is_known("Ramesh Kumar") is False
    assert resolver.is_known("Vikram Singh") is False
    assert resolver.is_known(None) is False


def test_is_known_rejects_a_merely_cleaned_name(resolver):
    """A name the resolver only tidied up is not evidence of a merchant."""
    assert resolver.resolve("ACME TECHNOLOGIES PVT LTD").method == "cleaned"
    assert resolver.is_known("ACME TECHNOLOGIES PVT LTD") is False


# --------------------------------------------------------------------------
# learning
# --------------------------------------------------------------------------


def test_learned_merchants_are_reused(resolver):
    assert resolver.resolve("KIRANA KING STORES").method == "cleaned"
    resolver.learn("Kirana King", ("kirana king stores", "kirana king"))
    match = resolver.resolve("KIRANA KING STORES")
    assert match.canonical == "Kirana King"
    assert match.method == "exact"
    assert resolver.is_known("KIRANA KING STORES") is True


def test_learning_invalidates_the_cache(resolver):
    resolver.resolve("NEW SHOP")  # populate the cache with the cleaned answer
    resolver.learn("New Shop Official", ("new shop",))
    assert resolver.resolve("NEW SHOP").canonical == "New Shop Official"


def test_default_resolver_is_shared():
    assert default_resolver() is default_resolver()
