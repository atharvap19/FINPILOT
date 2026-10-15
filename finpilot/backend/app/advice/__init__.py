"""Grounded recommendations over a job's ledger and segments.

The ledger and the clusters stay the source of truth. This package only
summarises them and, when a model is configured, asks it to write advice from
that summary. It never re-reads a document and never sends raw narrations or
person names to a model.
"""
