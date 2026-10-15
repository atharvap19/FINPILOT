"""CSV / XLSX extraction.

The hard part is not reading the file, it is finding the table inside it. A
bank export opens with ten to twenty rows of branch address, customer id and
IFSC before the header row appears, and closes with a blank row, a
``*** END OF STATEMENT ***`` and a totals line. Handing any of that to the
mapper produces nonsense, so this module locates the header row by scoring
candidates, and drops footer rows by asking whether they still look like
transactions.

Nothing here interprets values -- rows come out as strings exactly as the file
had them. Normalisation happens later, in :mod:`ingestion.normalize`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.detect import XLS, XLSX
from ingestion.mapping import CANONICAL_FIELDS, normalize_header
from ingestion.normalize import clean_text, is_blank, parse_amount, parse_date

#: How far into the file to look for the header row.
MAX_HEADER_SCAN = 45
#: Rows used to judge whether a candidate header is followed by real data.
LOOKAHEAD = 6

_HEADER_KEYWORDS: set[str] = set()
for _field in CANONICAL_FIELDS:
    for _alias in _field.aliases:
        _HEADER_KEYWORDS.add(normalize_header(_alias))

_FOOTER_MARKERS = (
    "end of statement",
    "computer generated",
    "does not require a signature",
    "statement summary",
    "this is a system generated",
    "please pay by the due date",
    "opening balance",
    "closing balance",
    "grand total",
)
_TOTAL_PREFIXES = ("total", "grand total", "sub total", "subtotal", "***", "---")


@dataclass
class RawRow:
    """One extracted row, values untouched."""

    index: int  # 1-based position among the data rows of this document
    values: dict[str, str]
    source_ref: str
    page: int | None = None
    grid_index: int = 0  # physical row in the file, for diagnostics

    def get(self, header: str | None) -> str:
        if not header:
            return ""
        return self.values.get(header, "")


@dataclass
class TabularExtraction:
    headers: list[str]
    rows: list[RawRow]
    header_row_index: int
    metadata_text: str = ""
    sheet_name: str | None = None
    dropped: list[tuple[int, str]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def sample_rows(self) -> list[list[str]]:
        """First ten rows as lists, for the column-mapping dialog."""
        return [[r.get(h) for h in self.headers] for r in self.rows[:10]]


# --------------------------------------------------------------------------
# reading the raw grid
# --------------------------------------------------------------------------


def read_grid(path: Path, file_type: str) -> list[list[str]]:
    """Every cell as a string, ragged rows padded to the widest row."""
    if file_type in (XLSX, XLS):
        grid = _read_excel(path)
    else:
        grid = _read_delimited(path)
    width = max((len(r) for r in grid), default=0)
    return [list(r) + [""] * (width - len(r)) for r in grid]


def _read_delimited(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[cell if cell is not None else "" for cell in row] for row in reader]


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:60])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Sniffer gives up on files whose metadata block has no delimiter at
        # all; count candidates on the busiest line instead.
        counts = {d: sample.count(d) for d in ",;\t|"}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def _read_excel(path: Path) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        out: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            out.append(["" if v is None else _excel_cell(v) for v in row])
        return out
    finally:
        wb.close()


def _excel_cell(value: object) -> str:
    """Excel hands back real dates and floats; keep them losslessly as text."""
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        if value.time() == _dt.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------
# finding the header row
# --------------------------------------------------------------------------


def score_header_row(grid: list[list[str]], index: int) -> float:
    """How much does row ``index`` look like the header of a transaction table?

    Three signals, because none is reliable alone: the cells match known column
    names, the row is densely filled, and the rows below it parse as data.
    """
    row = grid[index]
    cells = [clean_text(c) for c in row]
    filled = [c for c in cells if c]
    if len(filled) < 3:
        return 0.0

    keyword_hits = sum(1 for c in filled if normalize_header(c) in _HEADER_KEYWORDS)
    if keyword_hits < 2:
        return 0.0
    keyword_score = keyword_hits / len(filled)
    fill_score = len(filled) / max(len(cells), 1)

    # a header row itself should not be full of dates and amounts
    self_data = sum(
        1 for c in filled if parse_date(c) is not None or parse_amount(c) is not None
    )
    if self_data >= max(2, len(filled) // 2):
        return 0.0

    below = grid[index + 1 : index + 1 + LOOKAHEAD * 3]
    data_score = _data_likeness(below, len(filled))

    return round(0.45 * keyword_score + 0.20 * fill_score + 0.35 * data_score, 4)


def _data_likeness(rows: list[list[str]], expected_filled: int) -> float:
    """Blank rows are skipped rather than counted against the candidate --
    several banks put an empty row between the header and the first entry."""
    candidates = [r for r in rows if any(not is_blank(c) for c in r)][:LOOKAHEAD]
    if not candidates:
        return 0.0
    good = 0
    for row in candidates:
        cells = [clean_text(c) for c in row]
        filled = [c for c in cells if c]
        if len(filled) < max(2, expected_filled // 2):
            continue
        has_date = any(parse_date(c) is not None for c in filled)
        has_amount = any(parse_amount(c) is not None for c in filled)
        if has_date and has_amount:
            good += 1
    return good / len(candidates)


def score_header_row_structurally(grid: list[list[str]], index: int) -> float:
    """Score a header candidate without recognising any of its words.

    A bank nobody has a template for may still use column names nothing
    matches -- ``Col A``, or Hindi, or an internal code. Refusing to find the
    table at all in that case is wrong: the shape is the real evidence, and
    the mapping dialog exists precisely to name the columns afterwards.

    The shape is: this row is not itself data, the rows under it are, and they
    have about as many cells as it does.
    """
    row = grid[index]
    cells = [clean_text(c) for c in row]
    filled = [c for c in cells if c]
    if len(filled) < 2:
        return 0.0
    if any(parse_date(c) is not None or parse_amount(c) is not None for c in filled):
        return 0.0  # a header never holds a date or an amount

    below = [r for r in grid[index + 1 : index + 1 + LOOKAHEAD * 3]
             if any(not is_blank(c) for c in r)][:LOOKAHEAD]
    if len(below) < 2:
        return 0.0

    data_like = _data_likeness(below, len(filled))
    widths = [len([c for c in r if not is_blank(c)]) for r in below]
    width_match = sum(1 for w in widths if abs(w - len(filled)) <= 1) / len(widths)
    return round(0.65 * data_like + 0.35 * width_match, 4)


def find_header_row(grid: list[list[str]]) -> tuple[int, float]:
    """Index of the best header candidate, and its score. ``(-1, 0.0)`` if none.

    Two passes. The first recognises column names and is what matches a known
    bank layout. The second only looks at shape, and catches a layout whose
    headers mean nothing to us -- those rows still get extracted, and the user
    is asked to map them.

    The scan budget counts non-blank rows, so a metadata block padded with
    empty rows cannot push the real header out of range.
    """
    candidates = []
    scanned = 0
    for index, row in enumerate(grid):
        if all(is_blank(c) for c in row):
            continue
        scanned += 1
        if scanned > MAX_HEADER_SCAN:
            break
        candidates.append(index)

    best_index, best_score = -1, 0.0
    for index in candidates:
        score = score_header_row(grid, index)
        if score > best_score:
            best_index, best_score = index, score
    if best_score >= 0.45:
        return best_index, best_score

    best_index, best_score = -1, 0.0
    for index in candidates:
        score = score_header_row_structurally(grid, index)
        if score > best_score:
            best_index, best_score = index, score
    if best_score < 0.6:
        return -1, best_score
    return best_index, best_score


# --------------------------------------------------------------------------
# dropping footers
# --------------------------------------------------------------------------


def is_footer_row(cells: list[str], date_index: int | None,
                  markers: tuple[str, ...] = _FOOTER_MARKERS) -> str | None:
    """Return a reason if this row is footer noise, else ``None``."""
    filled = [c for c in cells if not is_blank(c)]
    if not filled:
        return "blank row"
    joined = " ".join(filled).lower()
    for marker in markers:
        if marker in joined:
            return f"footer marker {marker!r}"
    first = clean_text(filled[0]).lower()
    if any(first.startswith(p) for p in _TOTAL_PREFIXES):
        return "totals row"
    if date_index is not None and date_index < len(cells):
        if parse_date(cells[date_index]) is None:
            return "no parseable date"
    elif not any(parse_date(c) for c in filled):
        return "no parseable date"
    return None


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------


#: Fraction of rows under a candidate header that must hold a parseable date
#: before the table is treated as a list of transactions.
_DATE_COLUMN_RATIO = 0.6


def diagnose_no_table(grid: list[list[str]]) -> str:
    """Say *why* no transaction table was found, in words a user can act on.

    There is a real difference between "this file is not tabular at all" and
    "this is a perfectly good table that simply is not a list of transactions"
    -- a budget sheet of category totals, say. The second is not a failure and
    the message should not imply one.
    """
    header_index = _best_shaped_row(grid)
    if header_index is None:
        return "no table was found in this file"

    headers = [clean_text(c) for c in grid[header_index]]
    below = [r for r in grid[header_index + 1 :] if any(not is_blank(c) for c in r)]
    if len(below) < 2:
        return "a header row was found but there are no rows under it"

    if not _has_date_column(headers, below):
        named = ", ".join(h for h in headers if h)[:120]
        return (
            "this looks like a summary rather than a list of transactions: its "
            f"columns ({named}) include no dates, so there is nothing dated to "
            "import"
        )
    return "a table was found but its rows do not look like transactions"


def _best_shaped_row(grid: list[list[str]]) -> int | None:
    """The most plausible header by shape alone, for diagnosis only."""
    for index, row in enumerate(grid[:MAX_HEADER_SCAN]):
        cells = [clean_text(c) for c in row]
        filled = [c for c in cells if c]
        if len(filled) < 2:
            continue
        if any(parse_date(c) is not None or parse_amount(c) is not None for c in filled):
            continue
        if any(any(not is_blank(c) for c in r) for r in grid[index + 1 :]):
            return index
    return None


def _has_date_column(headers: list[str], rows: list[list[str]]) -> bool:
    for column in range(len(headers)):
        values = [r[column] for r in rows if column < len(r) and not is_blank(r[column])]
        if not values:
            continue
        dated = sum(1 for v in values if parse_date(v) is not None)
        if dated / len(values) >= _DATE_COLUMN_RATIO:
            return True
    return False


def extract_table(
    path: Path,
    file_type: str,
    *,
    filename: str | None = None,
    footer_markers: tuple[str, ...] = _FOOTER_MARKERS,
) -> TabularExtraction:
    """Pull the transaction table out of a CSV or spreadsheet."""
    name = filename or path.name
    grid = read_grid(path, file_type)
    if not grid:
        return TabularExtraction(headers=[], rows=[], header_row_index=-1,
                                 issues=["file is empty"])

    header_index, _score = find_header_row(grid)
    if header_index < 0:
        return TabularExtraction(
            headers=[], rows=[], header_row_index=-1,
            metadata_text=_grid_to_text(grid[:MAX_HEADER_SCAN]),
            issues=[diagnose_no_table(grid)],
        )

    headers = _dedupe_headers([clean_text(c) for c in grid[header_index]])
    metadata_text = _grid_to_text(grid[:header_index])
    date_index = _guess_date_column(headers)

    rows: list[RawRow] = []
    dropped: list[tuple[int, str]] = []
    stop = False
    for offset, raw in enumerate(grid[header_index + 1 :], start=header_index + 1):
        cells = [clean_text(c) for c in raw]
        reason = is_footer_row(cells, date_index, footer_markers)
        if reason:
            dropped.append((offset, reason))
            joined = " ".join(c for c in cells if c).lower()
            if any(m in joined for m in ("end of statement", "statement summary")):
                stop = True
            continue
        if stop:
            dropped.append((offset, "after end-of-statement marker"))
            continue
        index = len(rows) + 1
        rows.append(
            RawRow(
                index=index,
                values=dict(zip(headers, cells, strict=False)),
                source_ref=f"{name}#{index}",
                grid_index=offset,
            )
        )

    return TabularExtraction(
        headers=headers,
        rows=rows,
        header_row_index=header_index,
        metadata_text=metadata_text,
        dropped=dropped,
    )


def _dedupe_headers(headers: list[str]) -> list[str]:
    """Blank and repeated headers would collapse rows into one dict key."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, header in enumerate(headers):
        name = header or f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def _guess_date_column(headers: list[str]) -> int | None:
    for i, header in enumerate(headers):
        norm = normalize_header(header)
        if norm.startswith(("date", "txn date", "transaction date", "tran date", "posting date")):
            return i
    for i, header in enumerate(headers):
        if "date" in normalize_header(header):
            return i
    return None


def _grid_to_text(grid: list[list[str]]) -> str:
    return "\n".join(",".join(str(c) for c in row) for row in grid)
