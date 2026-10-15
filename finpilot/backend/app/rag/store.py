"""The vector store: one Chroma collection per job, persisted under DATA_DIR.

Embeddings are computed locally (Chroma's bundled ``all-MiniLM-L6-v2`` ONNX
model, the same model the rest of FinPilot names in ``EMBEDDING_MODEL``), so
indexing a document sends nothing anywhere. Only the passages the agent
retrieves for a question reach the model, and those are already masked.

Indexing is incremental and idempotent: a document is embedded once, keyed by
``doc_id``, and :func:`ensure_index` only adds what is missing -- so it is
cheap to call before every question, which is what makes a job uploaded before
this module existed searchable the first time someone asks about it.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import zlib
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.rag.chunking import Chunk, DocumentInfo, chunk_document

log = logging.getLogger("finpilot.rag")

Embedder = Callable[[Sequence[str]], list[list[float]]]

#: Documents in these states have nothing worth indexing (yet).
_SKIP_STATUSES = frozenset({"needs_password", "failed", "duplicate", "queued", "processing"})

_CLIENTS: dict[str, Any] = {}
_JOB_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()
_EMBEDDER: Embedder | None = None


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


def _default_embedder() -> Embedder:
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    function = DefaultEmbeddingFunction()

    def embed(texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, vector)) for vector in function(list(texts))]

    return embed


def hashing_embedder(dimensions: int = 256) -> Embedder:
    """A dependency-free bag-of-words embedder. Used by the tests, and good
    enough to keep search working if the ONNX model cannot be downloaded."""

    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * dimensions
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                # crc32, not hash(): str hashes change per process, and the
                # vectors are persisted
                vector[zlib.crc32(token.encode()) % dimensions] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    return embed


def set_embedder(embedder: Embedder | None) -> None:
    """Swap the embedder (tests). ``None`` restores the default."""
    global _EMBEDDER
    _EMBEDDER = embedder


def _embedder() -> Embedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            _EMBEDDER = _default_embedder()
        except Exception as exc:  # noqa: BLE001
            log.warning("local embedding model unavailable (%s); using hashing", exc)
            _EMBEDDER = hashing_embedder()
    return _EMBEDDER


# --------------------------------------------------------------------------
# collections
# --------------------------------------------------------------------------


def _client() -> Any:
    import chromadb

    path = get_settings().vectors_dir
    key = str(path)
    with _GUARD:
        client = _CLIENTS.get(key)
        if client is None:
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=key,
                settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=True),
            )
            _CLIENTS[key] = client
    return client


def _collection(job_id: str) -> Any:
    return _client().get_or_create_collection(
        name=job_id,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )


def _job_lock(job_id: str) -> threading.Lock:
    with _GUARD:
        return _JOB_LOCKS.setdefault(job_id, threading.Lock())


def reset_clients() -> None:
    """Forget cached clients (tests point DATA_DIR somewhere new)."""
    with _GUARD:
        _CLIENTS.clear()
        _JOB_LOCKS.clear()


# --------------------------------------------------------------------------
# indexing
# --------------------------------------------------------------------------


def _indexed_doc_ids(collection: Any) -> set[str]:
    got = collection.get(include=["metadatas"])
    return {m.get("doc_id") for m in (got.get("metadatas") or []) if m}


def _job_documents(job_id: str) -> tuple[list[DocumentInfo], dict[str, list[dict[str, Any]]]]:
    from app import jobs as job_service
    from app.db.models import StagingRow
    from app.db.session import session_scope

    with session_scope() as session:
        docs = [
            DocumentInfo.from_row(d)
            for d in job_service.job_documents(session, job_id)
            if d.status not in _SKIP_STATUSES
        ]
        staging: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            rows = session.scalars(
                select(StagingRow)
                .where(StagingRow.doc_id == doc.doc_id)
                .order_by(StagingRow.row_index)
            )
            staging[doc.doc_id] = [dict(r.payload or {}) for r in rows]
    return docs, staging


def ensure_index(job_id: str, *, rebuild: bool = False) -> dict[str, Any]:
    """Index whatever this job's documents are missing. Returns the status."""
    from app import jobs as job_service

    settings = get_settings()
    with _job_lock(job_id):
        if rebuild:
            drop_index(job_id)
        collection = _collection(job_id)
        docs, staging = _job_documents(job_id)
        wanted = {d.doc_id for d in docs}
        present = _indexed_doc_ids(collection)

        stale = present - wanted
        if stale:
            collection.delete(where={"doc_id": {"$in": sorted(stale)}})

        passwords = job_service.job_passwords(job_id)
        added = 0
        for doc in docs:
            if doc.doc_id in present:
                continue
            chunks = chunk_document(
                doc,
                staging=staging.get(doc.doc_id, []),
                password=passwords.get(doc.filename),
                chunk_chars=settings.rag_chunk_chars,
                rows_per_chunk=settings.rag_rows_per_chunk,
            )
            added += _add(collection, chunks)
        if added:
            log.info("index %s: added %d passages", job_id, added)
        return _status(collection, docs)


def _add(collection: Any, chunks: list[Chunk]) -> int:
    if not chunks:
        return 0
    seen: set[str] = set()
    unique = [c for c in chunks if not (c.chunk_id in seen or seen.add(c.chunk_id))]
    for start in range(0, len(unique), 64):
        batch = unique[start : start + 64]
        texts = [c.text for c in batch]
        collection.upsert(
            ids=[c.chunk_id for c in batch],
            documents=texts,
            metadatas=[c.metadata for c in batch],
            embeddings=_embedder()(texts),
        )
    return len(unique)


def _status(collection: Any, docs: list[DocumentInfo]) -> dict[str, Any]:
    got = collection.get(include=["metadatas"])
    per_doc: dict[str, int] = {}
    for meta in got.get("metadatas") or []:
        if meta:
            per_doc[meta.get("filename", "?")] = per_doc.get(meta.get("filename", "?"), 0) + 1
    return {
        "passages": len(got.get("ids") or []),
        "documents": len(per_doc),
        "documents_total": len(docs),
        "by_document": [{"filename": k, "passages": v} for k, v in sorted(per_doc.items())],
    }


def index_status(job_id: str) -> dict[str, Any]:
    docs, _ = _job_documents(job_id)
    return _status(_collection(job_id), docs)


def drop_index(job_id: str) -> None:
    try:
        _client().delete_collection(job_id)
    except Exception:  # noqa: BLE001 - nothing to drop is fine
        pass


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------


def search(
    job_id: str,
    query: str,
    *,
    k: int | None = None,
    document_type: str | None = None,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    """The ``k`` passages closest to ``query``, best first."""
    query = (query or "").strip()
    if not query:
        return []
    collection = _collection(job_id)
    count = collection.count()
    if not count:
        return []
    where: dict[str, Any] | None = None
    clauses = []
    if document_type:
        clauses.append({"document_type": document_type})
    if filename:
        clauses.append({"filename": filename})
    if len(clauses) == 1:
        where = clauses[0]
    elif clauses:
        where = {"$and": clauses}

    limit = max(1, min(k or get_settings().rag_top_k, 15, count))
    result = collection.query(
        query_embeddings=_embedder()([query]),
        n_results=limit,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict[str, Any]] = []
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    for text, meta, dist in zip(docs, metas, dists, strict=False):
        meta = meta or {}
        out.append(
            {
                "filename": meta.get("filename"),
                "document_type": meta.get("document_type"),
                "issuer": meta.get("issuer") or None,
                "locator": meta.get("locator"),
                "relevance": round(1.0 - float(dist), 3),
                "text": text,
            }
        )
    return out
