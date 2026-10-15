"""What a segment looks like, in aggregate.

This module is the privacy boundary. Everything downstream of it -- including
the model prompt in :mod:`segmentation.naming` -- sees only what is built here,
so the rule is enforced in one place: **a profile is statistics, never rows**.

Two consequences worth stating, because they are easy to undo by accident:

* No ``txn_id``, no ``source_ref``, no narration, no reference number and no
  balance ever enters a profile. A name for a spending habit does not need
  them, and principle 6 says nothing unmasked reaches a model prompt.
* Counterparty names are included **only** where ingestion resolved the row to
  a merchant. A row whose ``counterparty_type`` is ``person`` carries a human
  being's name -- the individual someone pays rent to -- so those are counted,
  never named. The profile says "6 payments to individuals", which is all the
  naming step needs to call the cluster "rent and transfers to people".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from segmentation.cluster import ClusterResult
from segmentation.features import FeatureMatrix, merchant_features

#: Hours treated as "late night" when summarising a segment's clock.
LATE_NIGHT_HOURS = frozenset({22, 23, 0, 1})

#: How many distinct merchants a profile names at most.
TOP_MERCHANTS = 6


@dataclass
class SegmentProfile:
    """One segment, described only in aggregate."""

    segment_id: int
    size: int
    share_of_rows: float
    mean_membership: float

    median_amount: float = 0.0
    p25_amount: float = 0.0
    p75_amount: float = 0.0
    max_amount: float = 0.0
    total_amount: float = 0.0

    debit_share: float = 0.0
    top_merchants: list[dict[str, Any]] = field(default_factory=list)
    person_counterparty_count: int = 0
    unnamed_counterparty_count: int = 0

    #: whatever category the source file already carried, via ``mcc``
    top_categories: list[dict[str, Any]] = field(default_factory=list)

    payment_modes: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] = field(default_factory=list)
    account_types: list[dict[str, Any]] = field(default_factory=list)

    rows_with_time: int = 0
    late_night_share: float = 0.0
    business_hours_share: float = 0.0
    weekend_share: float = 0.0
    top_days_of_month: list[dict[str, Any]] = field(default_factory=list)

    recurring_merchant_share: float = 0.0
    median_amount_variation: float = 0.0
    mean_interval_regularity: float = 0.0
    median_interval_days: float | None = None

    flag_shares: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _share(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _top(values: pd.Series, limit: int) -> list[dict[str, Any]]:
    counts = Counter(v for v in values.fillna("").astype(str) if v)
    total = sum(counts.values())
    return [
        {"value": value, "count": count, "share": _share(count, total)}
        for value, count in counts.most_common(limit)
    ]


def build_profile(
    segment_id: int,
    rows: pd.DataFrame,
    *,
    memberships: np.ndarray,
    total_rows: int,
    merch: pd.DataFrame,
) -> SegmentProfile:
    """Summarise one segment's rows. ``rows`` is a slice of the ledger frame."""
    amount = pd.to_numeric(rows["amount"], errors="coerce").dropna()
    date = pd.to_datetime(rows["date"], errors="coerce")

    profile = SegmentProfile(
        segment_id=segment_id,
        size=len(rows),
        share_of_rows=_share(len(rows), total_rows),
        mean_membership=round(float(np.mean(memberships)), 4) if len(memberships) else 0.0,
    )

    if not amount.empty:
        profile.median_amount = round(float(amount.median()), 2)
        profile.p25_amount = round(float(amount.quantile(0.25)), 2)
        profile.p75_amount = round(float(amount.quantile(0.75)), 2)
        profile.max_amount = round(float(amount.max()), 2)
        profile.total_amount = round(float(amount.sum()), 2)

    direction = rows.get("direction", pd.Series(dtype=str)).fillna("").astype(str)
    profile.debit_share = _share(int((direction == "debit").sum()), len(rows))

    # --- counterparties: merchants named, people only counted -------------
    ctype = rows.get("counterparty_type", pd.Series(dtype=str)).fillna("").astype(str)
    merchant = rows.get("merchant", pd.Series(dtype=str)).fillna("").astype(str)
    is_person = ctype == "person"
    profile.person_counterparty_count = int(is_person.sum())
    profile.unnamed_counterparty_count = int(((~is_person) & (merchant == "")).sum())
    profile.top_merchants = _top(merchant.where(~is_person, ""), TOP_MERCHANTS)

    # A category the source file supplied is a label the user already lives
    # with, so it is the best evidence a name can rest on.
    profile.top_categories = _top(rows.get("mcc", pd.Series(dtype=str)), 5)

    profile.payment_modes = _top(rows.get("payment_mode", pd.Series(dtype=str)), 4)
    profile.channels = _top(rows.get("channel", pd.Series(dtype=str)), 3)
    profile.account_types = _top(rows.get("account_type", pd.Series(dtype=str)), 3)

    # --- the clock --------------------------------------------------------
    if "time" in rows.columns:
        raw = rows["time"].astype("object").where(rows["time"].notna(), None)
        parsed = pd.to_datetime(raw, format="mixed", errors="coerce")
        hours = parsed.dt.hour.dropna().astype(int)
        profile.rows_with_time = int(len(hours))
        if len(hours):
            late = int(hours.isin(LATE_NIGHT_HOURS).sum())
            business = int(((hours >= 9) & (hours < 18)).sum())
            profile.late_night_share = _share(late, len(hours))
            profile.business_hours_share = _share(business, len(hours))

    dow = date.dt.dayofweek.dropna()
    if len(dow):
        profile.weekend_share = _share(int((dow >= 5).sum()), len(dow))

    days = date.dt.day.dropna().astype(int)
    if len(days):
        counts = Counter(days.tolist())
        profile.top_days_of_month = [
            {"day": int(day), "count": count, "share": _share(count, len(days))}
            for day, count in counts.most_common(3)
        ]

    # --- rhythm -----------------------------------------------------------
    slice_merch = merch.loc[rows.index]
    counts = slice_merch["merchant_count"]
    profile.recurring_merchant_share = _share(int((counts >= 3).sum()), len(rows))
    profile.median_amount_variation = round(
        float(slice_merch["merchant_amount_cv"].median()), 4
    )
    profile.mean_interval_regularity = round(
        float(slice_merch["merchant_interval_regularity"].mean()), 4
    )
    ordered = date.dropna().sort_values()
    if len(ordered) >= 3:
        gaps = ordered.diff().dt.days.dropna()
        if len(gaps):
            profile.median_interval_days = round(float(gaps.median()), 2)

    profile.flag_shares = {
        flag: _share(int(rows[flag].fillna(False).astype(bool).sum()), len(rows))
        for flag in (
            "is_transfer",
            "is_refund",
            "is_fee",
            "is_cash_withdrawal",
            "emi_flag",
        )
        if flag in rows.columns
    }
    return profile


def build_profiles(
    features: FeatureMatrix, result: ClusterResult
) -> list[SegmentProfile]:
    """One profile per real segment. Noise is summarised by the caller, not named."""
    frame = features.frame
    if frame.empty or not result.segment_ids:
        return []

    merch = merchant_features(frame)
    total = len(frame)
    profiles: list[SegmentProfile] = []
    for segment_id in result.segment_ids:
        positions = result.rows_for(segment_id)
        rows = frame.iloc[positions]
        profiles.append(
            build_profile(
                segment_id,
                rows,
                memberships=result.probabilities[positions],
                total_rows=total,
                merch=merch,
            )
        )
    return profiles
