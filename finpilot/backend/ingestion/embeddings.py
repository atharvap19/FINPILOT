"""Local sentence embeddings, for the two places fuzzy matching is not enough.

``all-MiniLM-L6-v2`` runs on the CPU with no API key, which makes it the right
tier between rapidfuzz and the model: it understands that ``Particulars`` and
``Narration`` mean the same thing, which character similarity never will, and
it costs nothing per call.

Two users:

* **header mapping** -- an unseen column header against the descriptions of the
  canonical fields.
* **merchant resolution** -- a leftover counterparty name against the known
  merchant list.

``sentence-transformers`` is an optional extra because it pulls in torch. When
it is not installed every function here degrades to "no opinion" rather than
failing, and the caller falls through to asking the user. That is checked by
:func:`available`, so a missing dependency is a documented downgrade rather
than a crash.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODEL: Any = None
_LOAD_FAILED = False


def available() -> bool:
    """Is the optional dependency installed?"""
    try:
        import sentence_transformers  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def get_model(name: str | None = None) -> Any:
    """Load the model once per process. ``None`` when it cannot be loaded.

    Loading is deliberately lazy: a job made only of known bank layouts never
    needs embeddings, and importing torch costs seconds.
    """
    global _MODEL, _LOAD_FAILED
    if _MODEL is not None or _LOAD_FAILED:
        return _MODEL
    with _LOCK:
        if _MODEL is not None or _LOAD_FAILED:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer

            from app.config import get_settings

            _MODEL = SentenceTransformer(name or get_settings().embedding_model)
        except Exception as exc:  # noqa: BLE001 - missing extra, no disk, no network
            log.info("embeddings unavailable (%s); falling back to fuzzy matching", exc)
            _LOAD_FAILED = True
            _MODEL = None
    return _MODEL


def reset() -> None:
    """Tests use this to force a reload."""
    global _MODEL, _LOAD_FAILED
    _MODEL = None
    _LOAD_FAILED = False


def encode(texts: list[str]) -> Any | None:
    """Normalised embeddings, or ``None`` when the model is unavailable."""
    if not texts:
        return None
    model = get_model()
    if model is None:
        return None
    try:
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding failed: %s", exc)
        return None


# --------------------------------------------------------------------------
# similarity
# --------------------------------------------------------------------------


@dataclass
class Similar:
    candidate: str
    score: float


def rank(query: str, candidates: list[str], *, top_k: int = 3) -> list[Similar]:
    """Candidates ranked by cosine similarity. Empty when unavailable.

    An empty list means "no opinion", never "no match" -- the caller must treat
    those differently, because the first is a reason to escalate and the second
    is a reason to stop.
    """
    if not query or not candidates:
        return []
    vectors = encode([query, *candidates])
    if vectors is None:
        return []
    import numpy as np

    query_vector, candidate_vectors = vectors[0], vectors[1:]
    scores = np.asarray(candidate_vectors) @ np.asarray(query_vector)
    order = scores.argsort()[::-1][:top_k]
    return [Similar(candidates[i], float(scores[i])) for i in order]


def best(query: str, candidates: list[str], *, threshold: float) -> Similar | None:
    ranked = rank(query, candidates, top_k=1)
    if not ranked or ranked[0].score < threshold:
        return None
    return ranked[0]


# --------------------------------------------------------------------------
# header mapping
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _field_descriptions() -> tuple[tuple[str, str], ...]:
    from ingestion.mapping import CANONICAL_FIELDS

    return tuple(
        (f.name, f"{f.name.replace('_', ' ')}: {f.description}")
        for f in CANONICAL_FIELDS
        if f.name != "ignore"
    )


def map_header(header: str, *, threshold: float = 0.72) -> tuple[str, float] | None:
    """Best canonical field for one unseen column header.

    Compared against the field *descriptions* rather than their names, because
    ``Particulars`` is nothing like ``description`` as a string but is very
    close to "the narration or particulars text describing the transaction".
    """
    pairs = _field_descriptions()
    ranked = rank(header, [text for _, text in pairs], top_k=1)
    if not ranked or ranked[0].score < threshold:
        return None
    name = next(n for n, text in pairs if text == ranked[0].candidate)
    return name, ranked[0].score


def map_headers(
    headers: list[str], *, threshold: float = 0.72
) -> dict[str, tuple[str, float]]:
    """Map several headers at once, one field per column."""
    out: dict[str, tuple[str, float]] = {}
    taken: set[str] = set()
    scored: list[tuple[float, str, str]] = []
    for header in headers:
        result = map_header(header, threshold=threshold)
        if result is not None:
            scored.append((result[1], header, result[0]))
    for score, header, name in sorted(scored, key=lambda t: -t[0]):
        if name in taken:
            continue
        taken.add(name)
        out[header] = (name, score)
    return out


# --------------------------------------------------------------------------
# merchant resolution
# --------------------------------------------------------------------------


@dataclass
class MerchantEmbeddingIndex:
    """Canonical merchant names, embedded once and reused.

    The last tier in :mod:`ingestion.merchants`: it catches the cases where the
    strings differ but the meaning does not.
    """

    names: list[str] = field(default_factory=list)
    _vectors: Any = None

    def build(self, names: list[str]) -> bool:
        self.names = list(names)
        self._vectors = encode(self.names)
        return self._vectors is not None

    @property
    def ready(self) -> bool:
        return self._vectors is not None and bool(self.names)

    def best(self, query: str, *, threshold: float = 0.80) -> Similar | None:
        if not self.ready or not query:
            return None
        vector = encode([query])
        if vector is None:
            return None
        import numpy as np

        scores = np.asarray(self._vectors) @ np.asarray(vector[0])
        index = int(scores.argmax())
        score = float(scores[index])
        if score < threshold:
            return None
        return Similar(self.names[index], score)
