"""Turn one uploaded document into masked passages.

Each kind of file is cut along its own natural seams, because a passage that
splits a bill's "Amount Payable" from its number, or a statement row from its
date, retrieves badly and reads worse:

* **statements and sheets** -- the table the extractor already located (the
  staging rows), in runs of ``rag_rows_per_chunk`` rows, each row written as
  ``header: value`` pairs so a passage is readable without its header row.
* **PDFs** -- page by page, then split on paragraph breaks to stay under
  ``rag_chunk_chars``. Bills are short enough to be one passage per page.
* **emails** -- subject, sender, date and the flattened body, one message at a
  time.

Every passage starts with a one-line header naming the file, its kind, the
issuer and where in the file it came from. That line is embedded with the text,
so "the HDFC card statement" retrieves the right file, and it is what the agent
cites back to the user.

Masking happens here, once, on the way in: nothing unmasked reaches the vector
store, and therefore nothing unmasked reaches a prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingestion.normalize import mask_tail, mask_text

log = logging.getLogger("finpilot.rag")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentInfo:
    """What chunking needs to know about a document. Duck-types the ORM row."""

    doc_id: str
    filename: str
    document_type: str = "unknown"
    issuer: str | None = None
    account: str | None = None
    statement_start: Any = None
    statement_end: Any = None
    stored_path: str | None = None
    file_type: str = ""

    @classmethod
    def from_row(cls, row: Any) -> DocumentInfo:
        return cls(
            doc_id=row.doc_id,
            filename=row.filename,
            document_type=row.document_type or "unknown",
            issuer=row.issuer,
            account=row.account,
            statement_start=row.statement_start,
            statement_end=row.statement_end,
            stored_path=row.stored_path,
            file_type=row.file_type or "",
        )

    @property
    def kind(self) -> str:
        suffix = Path(self.filename).suffix.lower()
        if suffix == ".pdf" or self.file_type == "application/pdf":
            return "pdf"
        if suffix in (".eml", ".mbox") or self.file_type in ("message/rfc822", "application/mbox"):
            return "email"
        if suffix in (".csv", ".xlsx", ".xls", ".tsv"):
            return "table"
        return "text"


def _header(doc: DocumentInfo, where: str) -> str:
    bits = [doc.filename, doc.document_type.replace("_", " ")]
    if doc.issuer:
        bits.append(doc.issuer)
    if doc.account:
        bits.append(f"account {doc.account}")
    if doc.statement_start and doc.statement_end:
        bits.append(f"{doc.statement_start} to {doc.statement_end}")
    bits.append(where)
    return "[" + " | ".join(str(b) for b in bits) + "]"


def _metadata(doc: DocumentInfo, where: str, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "filename": doc.filename,
        "document_type": doc.document_type,
        "issuer": doc.issuer or "",
        "locator": where,
    }
    # Chroma metadata values must be str/int/float/bool -- never None.
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


def split_text(text: str, limit: int) -> list[str]:
    """Split on blank lines, then lines, packing pieces up to ``limit`` chars."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for block in text.split("\n\n"):
        if len(block) <= limit:
            pieces.append(block)
            continue
        line_buf = ""
        for line in block.splitlines():
            while len(line) > limit:
                pieces.append(line[:limit])
                line = line[limit:]
            if len(line_buf) + len(line) + 1 > limit and line_buf:
                pieces.append(line_buf)
                line_buf = ""
            line_buf = f"{line_buf}\n{line}" if line_buf else line
        if line_buf:
            pieces.append(line_buf)

    out: list[str] = []
    buf = ""
    for piece in pieces:
        if len(buf) + len(piece) + 2 > limit and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{piece}" if buf else piece
    if buf:
        out.append(buf)
    return out


# --------------------------------------------------------------------------
# per kind
# --------------------------------------------------------------------------


def table_chunks(
    doc: DocumentInfo, rows: list[dict[str, Any]], *, rows_per_chunk: int
) -> list[Chunk]:
    """Staging rows (or any list of header->value dicts) -> passages."""
    out: list[Chunk] = []
    for start in range(0, len(rows), rows_per_chunk):
        window = rows[start : start + rows_per_chunk]
        first, last = start + 1, start + len(window)
        where = f"rows {first}-{last}"
        lines = []
        for payload in window:
            cells = [
                f"{key}: {_mask_line(str(value))}"
                for key, value in payload.items()
                if value not in (None, "") and str(value).strip()
            ]
            if cells:
                lines.append(" | ".join(cells))
        if not lines:
            continue
        out.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:r{first}",
                text=_header(doc, where) + "\n" + "\n".join(lines),
                metadata=_metadata(doc, where, row_start=first, row_end=last),
            )
        )
    return out


def pdf_chunks(doc: DocumentInfo, path: Path, *, password: str | None, limit: int) -> list[Chunk]:
    import pdfplumber

    out: list[Chunk] = []
    try:
        with pdfplumber.open(str(path), password=password or "") as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                text = mask_text_block(page.extract_text() or "")
                for part, piece in enumerate(split_text(text, limit), start=1):
                    where = f"page {number}" + (f" part {part}" if part > 1 else "")
                    out.append(
                        Chunk(
                            chunk_id=f"{doc.doc_id}:p{number}.{part}",
                            text=_header(doc, where) + "\n" + piece,
                            metadata=_metadata(doc, where, page=number),
                        )
                    )
    except Exception as exc:  # noqa: BLE001 - an unreadable PDF is not fatal
        log.warning("index: could not read %s (%s)", doc.filename, exc)
    return out


def email_chunks(doc: DocumentInfo, path: Path, *, limit: int) -> list[Chunk]:
    from ingestion.extractors.email_parser import parse_email_file

    out: list[Chunk] = []
    try:
        messages = parse_email_file(path, doc.file_type, filename=doc.filename)
    except Exception as exc:  # noqa: BLE001
        log.warning("index: could not parse %s (%s)", doc.filename, exc)
        return out
    for index, message in enumerate(messages, start=1):
        when = message.sent_at.isoformat(sep=" ", timespec="minutes") if message.sent_at else ""
        head = "\n".join(
            line
            for line in (
                f"Subject: {_mask_line(message.subject)}",
                f"From: {message.sender_domain or 'unknown sender'}",
                f"Date: {when}" if when else "",
                f"Kind: {message.kind}",
            )
            if line
        )
        body = mask_text_block(message.text)
        for part, piece in enumerate(split_text(body, limit) or [""], start=1):
            where = f"message {index}" + (f" part {part}" if part > 1 else "")
            out.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}:m{index}.{part}",
                    text=_header(doc, where) + "\n" + head + ("\n\n" + piece if piece else ""),
                    metadata=_metadata(doc, where, email_kind=message.kind),
                )
            )
    return out


def file_table_rows(path: Path) -> list[dict[str, Any]]:
    """Fallback for a sheet with no staging rows (budgets): read it as-is."""
    import pandas as pd

    try:
        if path.suffix.lower() in (".xlsx", ".xls"):
            sheets = pd.read_excel(path, sheet_name=None, dtype=str, header=None)
            frames = list(sheets.values())
        else:
            frames = [pd.read_csv(path, dtype=str, header=None, on_bad_lines="skip")]
    except Exception as exc:  # noqa: BLE001
        log.warning("index: could not read %s (%s)", path.name, exc)
        return []
    rows: list[dict[str, Any]] = []
    for frame in frames:
        frame = frame.dropna(how="all").fillna("")
        for values in frame.itertuples(index=False):
            cells = [str(v).strip() for v in values if str(v).strip()]
            if cells:
                rows.append({f"c{i + 1}": c for i, c in enumerate(cells)})
    return rows


#: Things a document carries that the ledger never stored, so mask_text was
#: never taught them: mobile numbers (bills print them as the consumer number),
#: labelled customer/consumer ids, and email addresses (the local part names a
#: person).
_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{4})[\s-]?(\d{5})(?!\d)")
_LABELLED_ID = re.compile(
    r"((?:consumer|customer|ca|crn|subscriber|relationship|account|a/c|policy|loan)"
    r"\s*(?:no\.?|number|id|#)?\s*[:\-]?\s*)([A-Z0-9][A-Z0-9\-/]{5,})",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+\.[\w.-]+)\b")


def _mask_line(line: str) -> str:
    line = mask_text(line)
    line = _EMAIL.sub(lambda m: f"XXXX@{m.group(1)}", line)
    line = _LABELLED_ID.sub(lambda m: m.group(1) + mask_tail(m.group(2)), line)
    line = _MOBILE.sub(lambda m: "XXXXXX" + m.group(2)[-4:], line)
    return line


def mask_text_block(text: str) -> str:
    """mask_text collapses whitespace, which would erase page layout, so mask
    each line on its own."""
    return "\n".join(_mask_line(line) for line in (text or "").splitlines() if line.strip())


def chunk_document(
    doc: DocumentInfo,
    *,
    staging: list[dict[str, Any]],
    password: str | None = None,
    chunk_chars: int = 1200,
    rows_per_chunk: int = 20,
) -> list[Chunk]:
    """One document -> its passages. Never raises."""
    path = Path(doc.stored_path) if doc.stored_path else None
    kind = doc.kind
    if kind == "table":
        rows = staging or (file_table_rows(path) if path and path.exists() else [])
        return table_chunks(doc, rows, rows_per_chunk=rows_per_chunk)
    if path is None or not path.exists():
        return []
    if kind == "pdf":
        return pdf_chunks(doc, path, password=password, limit=chunk_chars)
    if kind == "email":
        return email_chunks(doc, path, limit=chunk_chars)
    try:
        text = mask_text_block(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    return [
        Chunk(
            chunk_id=f"{doc.doc_id}:t{i}",
            text=_header(doc, f"part {i}") + "\n" + piece,
            metadata=_metadata(doc, f"part {i}"),
        )
        for i, piece in enumerate(split_text(text, chunk_chars), start=1)
    ]
