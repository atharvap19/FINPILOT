"""Segmentation: features, clustering, profiles, naming.

The naming tests never reach the network. ``GeminiNamer`` is replaced by a stub
so both paths are exercised deterministically: a model that answers, and a
model that fails and must fall back to rules.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from segmentation.cluster import NOISE_LABEL, cluster, resolve_min_cluster_size
from segmentation.features import CATEGORY_WEIGHT, build_features
from segmentation.naming import (
    SegmentName,
    build_prompt,
    describe,
    name_segments,
    rule_name,
)
from segmentation.profile import build_profiles
from segmentation.service import segment_frame


def make_frame(rows: list[dict]) -> pd.DataFrame:
    """A minimal ledger frame with the columns segmentation reads."""
    defaults = {
        "txn_id": "",
        "date": dt.date(2025, 6, 1),
        "time": None,
        "amount": 100.0,
        "direction": "debit",
        "account": "HDFC-XX7890",
        "account_type": "bank",
        "source_type": "bank_statement",
        "merchant": None,
        "counterparty_type": "unknown",
        "payment_mode": "UPI",
        "channel": "app",
        "mcc": None,
        "is_transfer": False,
        "is_refund": False,
        "is_fee": False,
        "is_cash_withdrawal": False,
        "emi_flag": False,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def synthetic_ledger() -> pd.DataFrame:
    """Three deliberately different habits, plus one genuine one-off."""
    rows: list[dict] = []
    n = 0
    # a fixed monthly subscription
    for month in range(1, 9):
        n += 1
        rows.append({
            "txn_id": f"t_sub_{n}", "date": dt.date(2025, month, 5),
            "amount": 649.0, "merchant": "Netflix", "counterparty_type": "merchant",
            "payment_mode": "CARD", "mcc": "Entertainment",
        })
    # frequent small grocery runs
    for day in range(1, 41):
        n += 1
        rows.append({
            "txn_id": f"t_gro_{n}",
            "date": dt.date(2025, 6, 1) + dt.timedelta(days=day),
            "amount": 220.0 + day, "merchant": "BigBasket",
            "counterparty_type": "merchant", "payment_mode": "UPI",
            "mcc": "Groceries",
        })
    # salary, same day each month
    for month in range(1, 9):
        n += 1
        rows.append({
            "txn_id": f"t_sal_{n}", "date": dt.date(2025, month, 28),
            "amount": 85000.0, "direction": "credit", "merchant": "Acme Payroll",
            "counterparty_type": "merchant", "payment_mode": "NEFT",
            "mcc": "Paycheck",
        })
    rows.append({
        "txn_id": "t_oneoff", "date": dt.date(2025, 7, 14), "amount": 41000.0,
        "merchant": "City Hospital", "counterparty_type": "merchant",
        "payment_mode": "CARD", "mcc": "Medical",
    })
    return make_frame(rows)


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------


def test_features_are_finite_and_aligned():
    frame = synthetic_ledger()
    f = build_features(frame)
    assert f.n_rows == len(frame)
    assert f.txn_ids == frame["txn_id"].tolist()
    assert f.matrix.shape[1] == len(f.columns)
    assert pd.notna(f.matrix).all()


def test_empty_frame_yields_empty_matrix():
    f = build_features(make_frame([]).iloc[0:0])
    assert f.n_rows == 0


def test_hour_is_zeroed_when_no_time_was_observed():
    """A row with no time must not cluster as if it happened at midnight."""
    frame = make_frame([
        {"txn_id": "t_a", "time": None},
        {"txn_id": "t_b", "time": "23:30:00"},
    ])
    f = build_features(frame)
    hour_sin = f.columns.index("hour_sin")
    has_time = f.columns.index("has_time")
    assert f.matrix[0][has_time] == 0.0
    assert f.matrix[1][has_time] == 1.0
    # the two rows must differ on the hour axis, which is the whole point
    assert f.matrix[0][hour_sin] != f.matrix[1][hour_sin]


def test_category_block_carries_its_weight():
    frame = synthetic_ledger()
    f = build_features(frame)
    columns = [c for c in f.columns if c.startswith("mcc=")]
    assert columns, "a ledger with categories must produce category columns"
    block = f.matrix[:, [f.columns.index(c) for c in columns]]
    assert block.max() == pytest.approx(CATEGORY_WEIGHT)


def test_missing_optional_columns_do_not_break_the_matrix():
    frame = make_frame([{"txn_id": f"t_{i}"} for i in range(5)])
    f = build_features(frame.drop(columns=["mcc", "channel", "merchant"]))
    assert f.n_rows == 5


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------


def test_min_cluster_size_scales_with_the_ledger():
    # the floor protects a small ledger from being declared all-noise
    assert resolve_min_cluster_size(137, 5) == 5
    assert resolve_min_cluster_size(800, 5) == 16
    # and the ceiling stops a huge ledger collapsing into a few blobs
    assert resolve_min_cluster_size(100_000, 5) == 60


def test_tiny_ledger_is_all_noise_rather_than_invented_segments():
    frame = make_frame([{"txn_id": f"t_{i}"} for i in range(6)])
    result = cluster(build_features(frame), min_cluster_size=5)
    assert result.params["fitted"] is False
    assert set(result.labels.tolist()) == {NOISE_LABEL}
    assert result.segment_ids == []


def test_distinct_habits_separate():
    result = cluster(build_features(synthetic_ledger()), min_cluster_size=5)
    assert len(result.segment_ids) >= 2

    assignment = result.assignment
    subs = {assignment[f"t_sub_{i}"][0] for i in range(1, 9)}
    assert len(subs) == 1, "a fixed monthly subscription is one habit"
    salary = {a for k, (a, _) in assignment.items() if k.startswith("t_sal_")}
    assert len(salary) == 1
    assert subs != salary, "salary and subscriptions are not the same habit"


def test_clustering_is_deterministic():
    frame = synthetic_ledger()
    first = cluster(build_features(frame), min_cluster_size=5)
    second = cluster(build_features(frame), min_cluster_size=5)
    assert first.labels.tolist() == second.labels.tolist()
    assert first.probabilities.tolist() == second.probabilities.tolist()


# --------------------------------------------------------------------------
# profiles: the privacy boundary
# --------------------------------------------------------------------------


def test_profile_never_carries_row_level_identifiers():
    frame = synthetic_ledger()
    features = build_features(frame)
    profiles = build_profiles(features, cluster(features, min_cluster_size=5))
    assert profiles
    for profile in profiles:
        rendered = str(profile.as_dict())
        assert "t_sub_" not in rendered
        assert "t_sal_" not in rendered
        assert "HDFC-XX7890" not in rendered


def test_people_are_counted_never_named():
    """A counterparty who is a person must not reach a model prompt by name."""
    rows = [
        {
            "txn_id": f"t_{i}",
            "date": dt.date(2025, 6, 1) + dt.timedelta(days=30 * i),
            "amount": 22000.0,
            "merchant": "Ramesh Kumar",
            "counterparty_type": "person",
        }
        for i in range(8)
    ]
    frame = make_frame(rows)
    features = build_features(frame)
    profiles = build_profiles(features, cluster(features, min_cluster_size=5))
    for profile in profiles:
        assert "Ramesh Kumar" not in str(profile.as_dict())
        assert "Ramesh" not in describe(profile)
        assert profile.person_counterparty_count > 0


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------


class StubNamer:
    """Stands in for GeminiNamer without touching the network."""

    max_calls = 50

    def __init__(self, reply=None, boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.prompts: list[str] = []

    def name_one(self, profile, taken):
        self.prompts.append(build_prompt(profile, taken))
        if self.boom:
            raise RuntimeError("model unavailable")
        return self.reply


def _profiles():
    frame = synthetic_ledger()
    features = build_features(frame)
    return build_profiles(features, cluster(features, min_cluster_size=5))


def test_model_names_are_used_when_the_call_succeeds():
    namer = StubNamer(reply=("monthly subscriptions", "same amount every month"))
    names = name_segments(_profiles(), namer)
    assert all(n.source == "llm" for n in names)
    assert names[0].name == "monthly subscriptions"


def test_a_failing_model_falls_back_to_rules_without_raising():
    names = name_segments(_profiles(), StubNamer(boom=True))
    assert names, "a failed naming call must still produce names"
    assert all(n.source == "rules" for n in names)
    assert all(n.name for n in names)


def test_no_namer_at_all_still_names_every_segment():
    profiles = _profiles()
    names = name_segments(profiles, None)
    assert len(names) == len(profiles)
    assert all(n.source == "rules" for n in names)


def test_duplicate_names_are_disambiguated():
    namer = StubNamer(reply=("everyday spending", "looks ordinary"))
    names = name_segments(_profiles(), namer)
    assert len({n.name for n in names}) == len(names)


def test_prompt_carries_names_already_taken():
    namer = StubNamer(reply=("groceries", ""))
    name_segments(_profiles(), namer)
    if len(namer.prompts) > 1:
        assert "already taken" in namer.prompts[1]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"is_fee": 0.9}, "bank fees and charges"),
        ({"is_cash_withdrawal": 0.8}, "cash withdrawals"),
        ({"is_refund": 0.7}, "refunds and reversals"),
        ({"emi_flag": 0.9}, "loan EMI payments"),
        ({"is_transfer": 0.8}, "transfers between accounts"),
    ],
)
def test_rule_names_read_the_dominant_flag(overrides, expected):
    profile = _profiles()[0]
    profile.flag_shares = overrides
    assert rule_name(profile) == expected


def test_rule_name_prefers_a_category_the_file_supplied():
    profile = _profiles()[0]
    profile.flag_shares = {}
    profile.debit_share = 1.0
    profile.recurring_merchant_share = 0.0
    profile.person_counterparty_count = 0
    profile.top_categories = [{"value": "Groceries", "count": 30, "share": 0.9}]
    assert rule_name(profile) == "groceries"


# --------------------------------------------------------------------------
# the service
# --------------------------------------------------------------------------


def test_segment_frame_assigns_every_row_exactly_once():
    frame = synthetic_ledger()
    result = segment_frame(frame)
    assert set(result.assignment) == set(frame["txn_id"])

    written = result.rows()
    assert len(written) == len(frame)
    for entry in written:
        assert 0.0 <= entry["segment_probability"] <= 1.0
        if entry["segment_id"] is None:
            assert entry["segment_name"] is None


def test_unplaced_rows_are_reported_not_hidden():
    result = segment_frame(synthetic_ledger())
    report = result.report
    placed = sum(s["size"] for s in report["segments_detail"])
    assert placed + report["unsegmented_rows"] == report["rows"]


def test_empty_ledger_segments_cleanly():
    result = segment_frame(make_frame([]).iloc[0:0])
    assert result.report["rows"] == 0
    assert result.rows() == []


def test_names_reach_the_written_rows():
    namer = StubNamer(reply=("monthly subscriptions", "fixed amount"))
    frame = synthetic_ledger()
    result = segment_frame(frame, namer=namer)
    named = [r for r in result.rows() if r["segment_id"] is not None]
    assert named
    assert all(r["segment_name"] for r in named)


def test_segment_name_fits_the_column():
    """``TransactionRow.segment_name`` is a 64-character column."""
    namer = StubNamer(reply=("x" * 500, "overlong"))
    result = segment_frame(synthetic_ledger(), namer=namer)
    for row in result.rows():
        if row["segment_name"]:
            assert len(row["segment_name"]) <= 64


def test_segment_name_objects_default_to_rules():
    assert SegmentName(segment_id=1, name="x").source == "rules"
