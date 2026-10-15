"""The segmentation stage, end to end.

    ledger rows -> features -> HDBSCAN -> profiles -> names -> ledger rows

Ingestion owns ``TRANSACTION_COLUMNS``; this stage owns three of the columns
ingestion deliberately left empty (``segment_id``, ``segment_name``,
``segment_probability``), and writes nothing else. A ledger that has been
segmented is the same ledger: no row is added, removed, merged or re-valued.

Rows HDBSCAN could not place keep ``segment_id`` -1 with no name. That is a
real answer -- a one-off payment is not a habit -- and it is reported in the
job's segmentation summary rather than hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from segmentation.cluster import NOISE_LABEL, ClusterResult, cluster
from segmentation.features import build_features
from segmentation.naming import GeminiNamer, SegmentName, name_segments
from segmentation.profile import SegmentProfile, build_profiles

log = logging.getLogger("finpilot.segmentation")


@dataclass
class SegmentationResult:
    #: txn_id -> (segment_id, membership strength)
    assignment: dict[str, tuple[int, float]] = field(default_factory=dict)
    profiles: list[SegmentProfile] = field(default_factory=list)
    names: list[SegmentName] = field(default_factory=list)
    clusters: ClusterResult | None = None
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def name_by_id(self) -> dict[int, str]:
        return {n.segment_id: n.name for n in self.names}

    def rows(self) -> list[dict[str, Any]]:
        """What to write back, one entry per transaction."""
        names = self.name_by_id
        return [
            {
                "txn_id": txn_id,
                "segment_id": None if segment_id == NOISE_LABEL else segment_id,
                "segment_name": names.get(segment_id),
                "segment_probability": round(probability, 4),
            }
            for txn_id, (segment_id, probability) in self.assignment.items()
        ]


def build_report(result: SegmentationResult) -> dict[str, Any]:
    clusters = result.clusters
    named = {n.segment_id: n for n in result.names}
    return {
        "rows": len(result.assignment),
        "segments": len(result.profiles),
        "unsegmented_rows": clusters.noise_count if clusters else 0,
        "params": clusters.params if clusters else {},
        "named_by_model": sum(1 for n in result.names if n.source == "llm"),
        "named_by_rules": sum(1 for n in result.names if n.source == "rules"),
        "segments_detail": [
            {
                "segment_id": profile.segment_id,
                "name": named[profile.segment_id].name
                if profile.segment_id in named
                else None,
                "rationale": named[profile.segment_id].rationale
                if profile.segment_id in named
                else "",
                "named_by": named[profile.segment_id].source
                if profile.segment_id in named
                else None,
                "size": profile.size,
                "share_of_rows": profile.share_of_rows,
                "median_amount": profile.median_amount,
                "total_amount": profile.total_amount,
                "mean_membership": profile.mean_membership,
                "profile": profile.as_dict(),
            }
            for profile in result.profiles
        ],
    }


def segment_frame(
    frame: pd.DataFrame,
    *,
    namer: GeminiNamer | None = None,
    min_cluster_size: int = 5,
    min_samples: int | None = None,
) -> SegmentationResult:
    """Segment a canonical transaction frame. Pure -- touches no database."""
    features = build_features(frame)
    if features.n_rows == 0:
        empty = SegmentationResult()
        empty.report = {"rows": 0, "segments": 0, "unsegmented_rows": 0, "params": {}}
        return empty

    clusters = cluster(
        features, min_cluster_size=min_cluster_size, min_samples=min_samples
    )
    profiles = build_profiles(features, clusters)
    names = name_segments(profiles, namer)

    result = SegmentationResult(
        assignment=clusters.assignment,
        profiles=profiles,
        names=names,
        clusters=clusters,
    )
    result.report = build_report(result)
    log.info(
        "segmentation: %d rows -> %d segments (%d unplaced), %d named by model",
        result.report["rows"],
        result.report["segments"],
        result.report["unsegmented_rows"],
        result.report["named_by_model"],
    )
    return result
