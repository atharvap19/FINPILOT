"""PDF statements that have a real text layer.

pdfplumber gives back one list of rows per ruled table per page. Three things
have to happen before those rows are usable:

* **pick the right table.** A statement page usually carries two or three --
  a summary box, the transaction grid, a rewards panel. Only the one that
  scores as a transaction table is wanted.
* **rejoin wrapped rows.** A long narration, or the foreign-currency detail
  under a card transaction, continues on the next line with an empty date
  cell. That continuation belongs to the row above, not to a row of its own.
* **read the balances.** Opening and closing balances live in the page text,
  not the table, and without them there is nothing to reconcile against.

Row numbering counts *physical* table rows, so a ``source_ref`` of
``card.pdf#1:4`` points at the fourth row of the grid on page one even after
its continuation line has been folded in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.extractors.tabular import (
    RawRow,
    TabularExtraction,
    _dedupe_headers,
    _guess_date_column,
    score_header_row,
)
from ingestion.normalize import clean_text, is_blank, parse_amount, parse_date

#: A table needs at least this header score to be treated as the transaction
#: grid rather than a summary box.
MIN_HEADER_SCORE = 0.45


@dataclass
class PdfTable:
    page: int
    headers: list[str]
    rows: list[list[str]]
    score: float


@dataclass
class PdfExtraction(TabularExtraction):
    pages: int = 0
    merged_continuations: int = 0
    page_text: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(self.page_text)


def extract_pdf_tables(
    path: Path,
    *,
    filename: str | None = None,
    password: str | None = None,
    footer_markers: tuple[str, ...] = (),
) -> PdfExtraction:
    """Pull the transaction grid out of every page of a text-layer PDF."""
    import pdfplumber

    name = filename or path.name
    headers: list[str] = []
    rows: list[RawRow] = []
    dropped: list[tuple[int, str]] = []
    page_text: list[str] = []
    issues: list[str] = []
    merged = 0

    with pdfplumber.open(path, password=password or "") as pdf:
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text.append(page.extract_text() or "")
            tables = page.extract_tables() or []
            best = _pick_transaction_table(tables, page_number)
            if best is None:
                continue
            if not headers:
                headers = _dedupe_headers([clean_text(c) for c in best.headers])
            page_rows, page_dropped, page_merged = _rows_from_table(
                best, headers, name, footer_markers
            )
            rows.extend(page_rows)
            dropped.extend(page_dropped)
            merged += page_merged

    if not headers:
        issues.append("no transaction table found in the PDF")

    return PdfExtraction(
        headers=headers,
        rows=rows,
        header_row_index=0,
        metadata_text="\n".join(page_text[:1]),
        dropped=dropped,
        issues=issues,
        pages=page_count,
        merged_continuations=merged,
        page_text=page_text,
    )


def _pick_transaction_table(tables: list[list[list]], page_number: int) -> PdfTable | None:
    """The table whose first row looks most like transaction column headers."""
    best: PdfTable | None = None
    for table in tables:
        grid = [[clean_text(c) for c in row] for row in table if row]
        if len(grid) < 2:
            continue
        score = score_header_row(grid, 0)
        if score < MIN_HEADER_SCORE:
            continue
        if best is None or score > best.score:
            best = PdfTable(page=page_number, headers=grid[0], rows=grid[1:], score=score)
    return best


def _rows_from_table(
    table: PdfTable,
    headers: list[str],
    filename: str,
    footer_markers: tuple[str, ...],
) -> tuple[list[RawRow], list[tuple[int, str]], int]:
    date_index = _guess_date_column(headers)
    out: list[RawRow] = []
    dropped: list[tuple[int, str]] = []
    merged = 0

    for position, cells in enumerate(table.rows, start=1):
        padded = list(cells) + [""] * (len(headers) - len(cells))

        # Order matters. A footer and a wrapped continuation both lack a date,
        # so the footer *wording* has to be ruled out first -- otherwise the
        # "system generated" line gets glued onto the last transaction.
        reason = _marker_reason(padded)
        if reason is None and footer_markers:
            joined = " ".join(c for c in padded if c).lower()
            if any(m in joined for m in footer_markers):
                reason = "footer marker"
        if reason:
            dropped.append((position, reason))
            continue

        if _is_continuation(padded, date_index):
            if out:
                _fold_into(out[-1], padded, headers)
                merged += 1
            else:
                dropped.append((position, "continuation with no row above it"))
            continue

        reason = _footer_reason(padded, date_index)
        if reason:
            dropped.append((position, reason))
            continue
        out.append(
            RawRow(
                index=position,
                values=dict(zip(headers, padded, strict=False)),
                source_ref=f"{filename}#{table.page}:{position}",
                page=table.page,
                grid_index=position,
            )
        )
    return out, dropped, merged


def _is_continuation(cells: list[str], date_index: int | None) -> bool:
    """A wrapped line: no date of its own, but carrying words.

    ``USD 12.00 @ 86.02`` under a foreign-currency card transaction is the
    common case. Note the test is for *letters*, not for "unparseable as an
    amount" -- that line contains numbers a money parser will happily read.
    """
    filled = [c for c in cells if not is_blank(c)]
    if not filled:
        return False
    if date_index is not None and date_index < len(cells):
        if parse_date(cells[date_index]) is not None:
            return False
    elif any(parse_date(c) for c in filled):
        return False
    return any(len(c) > 3 and sum(ch.isalpha() for ch in c) >= 2 for c in filled)


def _marker_reason(cells: list[str]) -> str | None:
    filled = [c for c in cells if not is_blank(c)]
    if not filled:
        return "blank row"
    joined = " ".join(filled).lower()
    for word in _FOOTER_WORDS:
        if word in joined:
            return f"footer marker {word!r}"
    return None


def _fold_into(row: RawRow, cells: list[str], headers: list[str]) -> None:
    """Append the continuation's text to the matching cells of the row above."""
    for header, value in zip(headers, cells, strict=False):
        value = clean_text(value)
        if not value:
            continue
        existing = row.values.get(header, "")
        row.values[header] = f"{existing} {value}".strip() if existing else value


_FOOTER_WORDS = (
    "system generated", "computer generated", "late payment", "finance charges",
    "end of statement", "total dues", "page ", "continued",
)


def _footer_reason(cells: list[str], date_index: int | None) -> str | None:
    filled = [c for c in cells if not is_blank(c)]
    if not filled:
        return "blank row"
    if date_index is not None and date_index < len(cells):
        if parse_date(cells[date_index]) is None:
            return "no parseable date"
    return None


# --------------------------------------------------------------------------
# balances out of the page text
# --------------------------------------------------------------------------

_BALANCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("opening", r"(?:previous|opening)\s+balance\s*:?\s*(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2})"),
    ("closing", r"(?:closing\s+balance|total\s+dues|total\s+amount\s+due)\s*:?\s*"
                r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2})"),
)


def read_balances(text: str) -> dict[str, object]:
    """Opening / closing balance from the statement's summary text.

    Card statements label these ``Previous Balance`` and ``Total Dues``; bank
    statements use ``Opening``/``Closing Balance``. Both are handled, because
    the reconciliation needs one of each whatever the document calls them.
    """
    out: dict[str, object] = {}
    lowered = text.lower()
    for key, pattern in _BALANCE_PATTERNS:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            out[f"{key}_balance"] = parse_amount(match.group(1))
    return out


_SUMMARY_ROW = re.compile(
    r"previous\s+balance\s+payments?\s*/?\s*credits?\s+purchases?\s*/?\s*debits?", re.IGNORECASE
)


def read_summary_table(tables: list[list[list]]) -> dict[str, object]:
    """Card statements print the summary as its own little table."""
    for table in tables or []:
        grid = [[clean_text(c) for c in row] for row in table if row]
        if len(grid) < 2:
            continue
        header = " ".join(grid[0]).lower()
        if "previous balance" not in header:
            continue
        values = grid[1]
        out: dict[str, object] = {}
        for label, value in zip(grid[0], values, strict=False):
            amount = parse_amount(value)
            if amount is None:
                continue
            key = label.lower()
            if "previous" in key:
                out["opening_balance"] = amount
            elif "total dues" in key or "total amount due" in key:
                out["closing_balance"] = amount
        if out:
            return out
    return {}
