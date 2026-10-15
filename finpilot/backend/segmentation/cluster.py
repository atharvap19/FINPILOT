"""HDBSCAN over the feature matrix.

Why HDBSCAN rather than KMeans or a mixture model:

* it chooses the number of segments itself, and the right number is a property
  of the user's spending, not something this code can know in advance;
* it has a notion of **noise**. A one-off hospital payment is not a habit, and
  forcing it into the nearest cluster would both mislabel that row and drag the
  cluster's profile away from what it actually represents. Those rows come back
  with ``segment_id`` -1 and no name, which is an honest answer;
* it reports a membership strength per row, which is what the ledger's
  ``segment_probability`` column was reserved for.

``sklearn.cluster.HDBSCAN`` is used rather than the standalone ``hdbscan``
package: it ships in the scikit-learn wheel, so there is no compiler to find on
Windows, and it exposes the same ``labels_`` / ``probabilities_`` pair.

The fit is deterministic -- the same ledger clusters the same way every time,
which is what makes a saved ``segment_id`` worth storing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from segmentation.features import FeatureMatrix

#: Rows HDBSCAN could not place in any segment.
NOISE_LABEL = -1


@dataclass
class ClusterResult:
    """Per-row assignment, in the same order as the feature matrix."""

    txn_ids: list[str]
    labels: np.ndarray
    probabilities: np.ndarray
    #: parameters actually used, echoed for the report
    params: dict[str, object] = field(default_factory=dict)

    @property
    def segment_ids(self) -> list[int]:
        """Distinct real segments, noise excluded, in ascending order."""
        return sorted({int(x) for x in self.labels if int(x) != NOISE_LABEL})

    @property
    def noise_count(self) -> int:
        return int((self.labels == NOISE_LABEL).sum())

    @property
    def assignment(self) -> dict[str, tuple[int, float]]:
        """txn_id -> (segment_id, membership strength)."""
        return {
            txn_id: (int(label), float(prob))
            for txn_id, label, prob in zip(
                self.txn_ids, self.labels, self.probabilities, strict=True
            )
        }

    def rows_for(self, segment_id: int) -> list[int]:
        """Positional indices of the rows in one segment."""
        return [i for i, label in enumerate(self.labels) if int(label) == segment_id]


def resolve_min_cluster_size(n_rows: int, configured: int) -> int:
    """Keep the smallest allowed segment sensible for the ledger's size.

    Roughly 2% of the ledger, with ``configured`` as an absolute floor and 60 as
    a ceiling. Both bounds were needed in practice: held at a fixed 15, a
    137-row ledger produced no segments at all -- every candidate group was
    smaller than the minimum, so the whole ledger came back as noise -- while an
    unbounded 2% would let a multi-year ledger collapse into three enormous
    blobs that describe nothing.
    """
    if not n_rows:
        return configured
    return max(configured, min(int(round(n_rows * 0.02)), 60))


def cluster(
    features: FeatureMatrix,
    *,
    min_cluster_size: int = 5,
    min_samples: int | None = None,
    cluster_selection_method: str = "eom",
) -> ClusterResult:
    """Fit HDBSCAN and return the per-row assignment.

    A ledger too small to hold even one segment of the minimum size is returned
    entirely as noise rather than being forced into clusters that would only be
    describing sampling accident.
    """
    from sklearn.cluster import HDBSCAN

    n_rows = features.n_rows
    if n_rows == 0:
        return ClusterResult(
            txn_ids=[],
            labels=np.empty(0, dtype=int),
            probabilities=np.empty(0, dtype=float),
            params={"fitted": False, "reason": "no rows"},
        )

    effective = resolve_min_cluster_size(n_rows, min_cluster_size)
    if n_rows < effective * 2:
        return ClusterResult(
            txn_ids=list(features.txn_ids),
            labels=np.full(n_rows, NOISE_LABEL, dtype=int),
            probabilities=np.zeros(n_rows, dtype=float),
            params={
                "fitted": False,
                "reason": f"{n_rows} rows cannot support a segment of {effective}",
                "min_cluster_size": effective,
            },
        )

    model = HDBSCAN(
        min_cluster_size=effective,
        min_samples=min_samples,
        cluster_selection_method=cluster_selection_method,
        metric="euclidean",
        copy=True,
    ).fit(features.matrix)

    return ClusterResult(
        txn_ids=list(features.txn_ids),
        labels=model.labels_.astype(int),
        probabilities=model.probabilities_.astype(float),
        params={
            "fitted": True,
            "min_cluster_size": effective,
            "min_samples": min_samples,
            "cluster_selection_method": cluster_selection_method,
            "n_features": features.n_features,
        },
    )
