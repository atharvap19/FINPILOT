"""Phase C: group the ledger into behaviour patterns, then name them.

Ingestion produces the ledger with code alone. This stage reads that ledger and
fills the three columns reserved for it -- ``segment_id``, ``segment_name`` and
``segment_probability`` -- in two steps that are deliberately kept apart:

* :mod:`segmentation.cluster` decides *which rows belong together*, with
  HDBSCAN over the features in :mod:`segmentation.features`. No model is
  involved, and the fit is deterministic.
* :mod:`segmentation.naming` decides *what to call each group*, with a model,
  from the aggregate profile built in :mod:`segmentation.profile` and nothing
  else.

Keeping them apart is the point: a bad name is cosmetic and can be regenerated,
while the grouping it describes stays reproducible and explainable.
"""

from segmentation.cluster import NOISE_LABEL, ClusterResult, cluster
from segmentation.features import FeatureMatrix, build_features
from segmentation.naming import GeminiNamer, SegmentName, name_segments, rule_name
from segmentation.profile import SegmentProfile, build_profiles
from segmentation.service import SegmentationResult, segment_frame

__all__ = [
    "NOISE_LABEL",
    "ClusterResult",
    "FeatureMatrix",
    "GeminiNamer",
    "SegmentName",
    "SegmentProfile",
    "SegmentationResult",
    "build_features",
    "build_profiles",
    "cluster",
    "name_segments",
    "rule_name",
    "segment_frame",
]
