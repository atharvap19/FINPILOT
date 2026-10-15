"""The document index: every uploaded file, cut into passages, in a vector store.

The ledger answers "how much"; the documents answer "what did that bill say",
"what was in that order", "which statement shows it". The query agent and the
report writer reach the documents through :func:`app.rag.store.search`, and only
ever see passages that have been through :func:`ingestion.normalize.mask_text`
-- the same masking the ledger gets before anything is stored.
"""
