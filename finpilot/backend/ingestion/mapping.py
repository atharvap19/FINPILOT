"""Phase A step 3: which column is which?

A bank's export layout is stable, so the cheap path is a lookup: fingerprint
the header row, find the matching template in ``templates/*.json``, done. Only a
layout nobody has seen before costs anything, and then the ladder is

    exact fingerprint  ->  loose fingerprint  ->  keyword match
                       ->  embeddings (ingestion/embeddings.py)
                       ->  the user confirms it in the UI

and whatever the user confirms is written back out as a new template, so the
next file of that shape takes the cheap path.

Three amount styles have to be supported, because Indian banks use all three:
separate debit/credit columns, one signed amount, and one amount with a
``Dr``/``Cr`` suffix or flag column.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# --------------------------------------------------------------------------
# canonical fields
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalField:
    """One target a source column can be mapped to.

    ``description`` is what the embedding model is compared against, so it is
    written for a reader who has never met this bank's vocabulary.
    """

    name: str
    description: str
    aliases: tuple[str, ...]
    required: bool = False


CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        "date",
        "the date the transaction happened",
        ("date", "txn date", "transaction date", "tran date", "posting date", "date of transaction",
         "trans date", "dt"),
        required=True,
    ),
    CanonicalField(
        "value_date",
        "the date the transaction was value-dated or settled, if shown separately",
        ("value date", "value dt", "val date", "value date/posting date"),
    ),
    CanonicalField(
        "time",
        "the time of day of the transaction",
        ("time", "transaction time", "txn time"),
    ),
    CanonicalField(
        "description",
        "the narration or particulars text describing the transaction",
        ("narration", "description", "particulars", "transaction details", "details", "remarks",
         "transaction description", "transaction remarks", "transaction", "narrative"),
        required=True,
    ),
    CanonicalField(
        "reference_no",
        "the bank reference, UPI reference, UTR or cheque number",
        ("chq./ref.no.", "chq/ref no", "ref no", "ref no./cheque no.", "reference", "reference no",
         "reference number", "cheque no", "chq no", "utr", "transaction id", "txn id", "ref"),
    ),
    CanonicalField(
        "debit",
        "money leaving the account, in its own column",
        ("withdrawal amt.", "withdrawal", "withdrawal amount", "debit", "debit amount", "dr",
         "withdrawals", "paid out", "debit(dr)"),
    ),
    CanonicalField(
        "credit",
        "money arriving in the account, in its own column",
        ("deposit amt.", "deposit", "deposit amount", "credit", "credit amount", "cr", "deposits",
         "paid in", "credit(cr)"),
    ),
    CanonicalField(
        "amount",
        "a single amount column, either signed or with a separate debit/credit indicator",
        ("amount", "amount (in rs.)", "amount(inr)", "transaction amount", "amt", "value",
         "amount in inr", "amount rs"),
    ),
    CanonicalField(
        "dr_cr_flag",
        "a column holding Dr or Cr saying which way the money went",
        ("dr/cr", "cr/dr", "type", "txn type", "transaction type", "debit/credit", "indicator"),
    ),
    CanonicalField(
        "balance_after",
        "the running account balance after this transaction",
        ("closing balance", "balance", "running balance", "available balance", "balance (inr)",
         "bal", "closing bal", "balance amount"),
    ),
    CanonicalField(
        "merchant",
        "the merchant or payee name, when the file gives it as its own column",
        ("merchant", "payee", "paid to", "vendor", "beneficiary", "merchant name"),
    ),
    CanonicalField(
        "mcc",
        "the card network merchant category code",
        ("mcc", "merchant category code", "category code"),
    ),
    CanonicalField(
        "currency",
        "the currency of the amount",
        ("currency", "ccy", "curr"),
    ),
    CanonicalField(
        "account",
        "the account or card the row belongs to, when one file covers several",
        ("account", "account no", "account number", "card no", "card number", "a/c"),
    ),
    CanonicalField(
        "payment_mode",
        "how the payment was made, e.g. UPI, card, cash",
        ("mode", "payment mode", "method", "payment method", "paid via"),
    ),
    CanonicalField(
        "note",
        "a free-text note the user wrote about the transaction",
        ("note", "notes", "comment", "comments", "memo", "tag"),
    ),
    CanonicalField(
        "ignore",
        "a column with nothing useful in it",
        ("sl no", "s.no", "srno", "sr no", "#", "index", "unnamed",
         "bill id", "bill no", "invoice id", "invoice no", "invoice number"),
    ),
)

FIELD_BY_NAME = {f.name: f for f in CANONICAL_FIELDS}
CANONICAL_FIELD_NAMES = tuple(f.name for f in CANONICAL_FIELDS)

#: A mapping is usable only if it can answer "when", "what" and "how much".
REQUIRED_FIELDS = ("date", "description")
AMOUNT_FIELDS = ("debit", "credit", "amount")

#: confidence at or above which a proposed column mapping is accepted without
#: asking anybody
AUTO_ACCEPT = 0.90


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_header(text: object) -> str:
    """``  Withdrawal Amt. `` -> ``withdrawal amt``."""
    cleaned = _NON_ALNUM.sub(" ", str(text or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def header_fingerprint(headers: list[str]) -> str:
    """Exact layout identity: same columns, same order."""
    tokens = [normalize_header(h) for h in headers]
    return hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:16]


def loose_fingerprint(headers: list[str]) -> str:
    """Same columns in any order, ignoring empty ones.

    Catches the common case of a bank adding a blank trailing column or
    reordering two columns between exports.
    """
    tokens = sorted({normalize_header(h) for h in headers if normalize_header(h)})
    return hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------


@dataclass
class Template:
    name: str
    columns: dict[str, str]  # canonical field -> header text as it appears
    amount_style: str  # debit_credit | signed | dr_cr_suffix | dr_cr_flag
    issuer: str | None = None
    document_type: str = "bank_statement"
    account_type: str = "bank"
    narration_dialect: str = "generic"
    dayfirst: bool = True
    header_tokens: list[str] = field(default_factory=list)
    footer_markers: list[str] = field(default_factory=list)
    source: str = "builtin"

    @property
    def fingerprint(self) -> str:
        return header_fingerprint(self.header_tokens)

    @property
    def loose(self) -> str:
        return loose_fingerprint(self.header_tokens)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["fingerprint"] = self.fingerprint
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Template:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class TemplateLibrary:
    """Templates shipped with the code plus templates users have confirmed."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or TEMPLATES_DIR
        self._templates: list[Template] = []
        self.reload()

    def reload(self) -> None:
        self._templates = []
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                self._templates.append(Template.from_json(data))
            except TypeError:
                continue

    def add(self, template: Template) -> None:
        self._templates.append(template)

    @property
    def templates(self) -> list[Template]:
        return list(self._templates)

    def find(self, headers: list[str]) -> tuple[Template | None, str]:
        """Returns ``(template, how it matched)``."""
        fp = header_fingerprint(headers)
        for tpl in self._templates:
            if tpl.fingerprint == fp:
                return tpl, "exact"
        loose = loose_fingerprint(headers)
        for tpl in self._templates:
            if tpl.loose == loose:
                return tpl, "loose"
        return None, "none"

    def save(self, template: Template) -> Path:
        """Persist a learned template so a fresh database still has it."""
        self.directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", template.name.lower()).strip("_")
        path = self.directory / f"{slug}.json"
        path.write_text(json.dumps(template.to_json(), indent=2), encoding="utf-8")
        self.add(template)
        return path


# --------------------------------------------------------------------------
# proposing a mapping for an unseen layout
# --------------------------------------------------------------------------


@dataclass
class ColumnProposal:
    header: str
    field: str
    confidence: float
    reason: str

    @property
    def is_confident(self) -> bool:
        return self.confidence >= AUTO_ACCEPT


@dataclass
class ProposedMapping:
    proposals: list[ColumnProposal]
    amount_style: str
    #: headers whose mapping nobody is sure about -- these go to the user
    uncertain: list[str] = field(default_factory=list)
    #: what the proposal was made about, carried so the mapping dialog can
    #: show the columns and the first rows without re-reading the file
    headers: list[str] = field(default_factory=list)
    sample_rows: list[list[str]] = field(default_factory=list)

    @property
    def columns(self) -> dict[str, str]:
        """canonical field -> header, keeping the best proposal per field."""
        best: dict[str, ColumnProposal] = {}
        for p in self.proposals:
            if p.field == "ignore":
                continue
            current = best.get(p.field)
            if current is None or p.confidence > current.confidence:
                best[p.field] = p
        return {fieldname: p.header for fieldname, p in best.items()}

    @property
    def is_usable(self) -> bool:
        cols = self.columns
        has_required = all(f in cols for f in REQUIRED_FIELDS)
        has_amount = any(f in cols for f in AMOUNT_FIELDS)
        # an extra column we would only drop (an invoice id, a notes field)
        # is no reason to stop and ask; an uncertain guess at a real field is
        blocking = [p for p in self.proposals if p.header in self.uncertain and p.field != "ignore"]
        return has_required and has_amount and not blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "amount_style": self.amount_style,
            "uncertain": self.uncertain,
            "columns": self.columns,
            "proposals": [asdict(p) for p in self.proposals],
            "headers": self.headers,
            "sample_rows": self.sample_rows,
        }


_DR_CR_SUFFIX = re.compile(r"\b(dr|cr)\b\.?\s*$", re.IGNORECASE)
_SIGNED = re.compile(r"^\s*[-(−]")


def propose_mapping(headers: list[str], sample_rows: list[list[str]] | None = None) -> ProposedMapping:
    """Map by header keyword, then infer how the amount is expressed.

    This is the deterministic tier. Columns it is not confident about are left
    in ``uncertain`` for the embedding tier and finally for the user.
    """
    sample_rows = sample_rows or []
    proposals: list[ColumnProposal] = []
    taken: set[str] = set()

    scored: list[tuple[float, str, int, str]] = []
    for index, header in enumerate(headers):
        norm = normalize_header(header)
        if not norm:
            proposals.append(ColumnProposal(header, "ignore", 1.0, "empty header"))
            continue
        for fieldname, score, reason in _score_header(norm):
            scored.append((score, fieldname, index, reason))

    # assign greedily, best score first, one column per field
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    assigned: dict[int, ColumnProposal] = {}
    for score, fieldname, index, reason in scored:
        if index in assigned or fieldname in taken:
            continue
        if score < 0.45:
            continue
        assigned[index] = ColumnProposal(headers[index], fieldname, score, reason)
        if fieldname != "ignore":
            taken.add(fieldname)

    uncertain: list[str] = []
    for index, header in enumerate(headers):
        if not normalize_header(header):
            continue
        proposal = assigned.get(index)
        if proposal is None:
            column_values = [r[index] for r in sample_rows if index < len(r)]
            proposal = _guess_from_values(header, column_values)
            uncertain.append(header)
        elif not proposal.is_confident:
            uncertain.append(header)
        proposals.append(proposal)

    style = infer_amount_style(
        {p.field for p in proposals}, headers, sample_rows,
        amount_index=next((i for i, h in enumerate(headers)
                           if any(p.header == h and p.field == "amount" for p in proposals)), None),
    )
    return ProposedMapping(
        proposals=proposals,
        amount_style=style,
        uncertain=uncertain,
        headers=list(headers),
        sample_rows=[list(r) for r in sample_rows[:10]],
    )


def _score_header(norm: str) -> list[tuple[str, float, str]]:
    """Score one normalised header against every canonical field."""
    out: list[tuple[str, float, str]] = []
    for cfield in CANONICAL_FIELDS:
        best = 0.0
        reason = ""
        for alias in cfield.aliases:
            alias_norm = normalize_header(alias)
            if not alias_norm:
                # an alias like "#" normalises to nothing, and every header
                # "starts with" nothing -- it would claim every column
                continue
            if norm == alias_norm:
                best, reason = 1.0, f"exact alias {alias!r}"
                break
            if norm.startswith(alias_norm) or alias_norm.startswith(norm):
                score = 0.93 - 0.02 * abs(len(norm) - len(alias_norm))
                if score > best:
                    best, reason = max(score, 0.75), f"prefix of alias {alias!r}"
            ratio = fuzz.token_set_ratio(norm, alias_norm) / 100.0
            if ratio > best:
                best, reason = ratio * 0.9, f"fuzzy alias {alias!r}"
        if best > 0:
            out.append((cfield.name, round(best, 4), reason))
    return out


_DATE_ISH = re.compile(r"\d{1,4}[-/ ][A-Za-z0-9]{1,9}[-/ ]\d{2,4}")
_NUMERIC_ISH = re.compile(r"^\s*[₹(\-]?\s*[\d,]+\.?\d*\s*\)?\s*(dr|cr)?\.?\s*$", re.IGNORECASE)


def _guess_from_values(header: str, values: list[str]) -> ColumnProposal:
    """Last deterministic resort: look at what is actually in the column."""
    filled = [v for v in values if str(v).strip()]
    if not filled:
        return ColumnProposal(header, "ignore", 0.6, "column is empty")
    date_like = sum(1 for v in filled if _DATE_ISH.search(str(v)))
    numeric = sum(1 for v in filled if _NUMERIC_ISH.match(str(v)))
    if date_like / len(filled) > 0.8:
        return ColumnProposal(header, "date", 0.55, "values look like dates")
    if numeric / len(filled) > 0.8:
        return ColumnProposal(header, "amount", 0.5, "values look like amounts")
    longest = max(len(str(v)) for v in filled)
    if longest > 25:
        return ColumnProposal(header, "description", 0.5, "values look like free text")
    return ColumnProposal(header, "ignore", 0.3, "no signal")


def infer_amount_style(
    fields: set[str],
    headers: list[str],
    sample_rows: list[list[str]],
    amount_index: int | None = None,
) -> str:
    """Which of the three amount conventions this file uses."""
    if "debit" in fields and "credit" in fields:
        return "debit_credit"
    if "dr_cr_flag" in fields:
        return "dr_cr_flag"
    if amount_index is not None:
        values = [str(r[amount_index]) for r in sample_rows if amount_index < len(r)]
        values = [v for v in values if v.strip()]
        if values and sum(1 for v in values if _DR_CR_SUFFIX.search(v)) / len(values) > 0.5:
            return "dr_cr_suffix"
        if values and any(_SIGNED.match(v) for v in values):
            return "signed"
    if "amount" in fields:
        return "signed"
    return "debit_credit"


def refine_proposal(
    proposal: ProposedMapping,
    headers: list[str],
    sample_rows: list[list[str]],
    *,
    embedding_threshold: float = 0.72,
) -> ProposedMapping:
    """Take an uncertain keyword mapping further, without asking the user yet.

    The one tier beyond keywords is local sentence embeddings, which understand
    that ``Particulars`` means ``description`` where character similarity never
    will. Anything still uncertain after that stays uncertain and the UI asks --
    guessing a column mapping silently is how a whole statement ends up wrong in
    a way nobody notices.
    """
    if not proposal.uncertain:
        return proposal

    from ingestion import embeddings as emb

    taken = {p.field for p in proposal.proposals if p.field != "ignore" and p.is_confident}
    resolved: dict[str, ColumnProposal] = {}

    if emb.available():
        for header, (name, score) in emb.map_headers(
            proposal.uncertain, threshold=embedding_threshold
        ).items():
            if name in taken:
                continue
            taken.add(name)
            resolved[header] = ColumnProposal(
                header, name, round(float(score), 3), "embedding similarity"
            )

    if not resolved:
        return proposal

    merged = [p for p in proposal.proposals if p.header not in resolved]
    merged.extend(resolved.values())
    return ProposedMapping(
        proposals=merged,
        amount_style=proposal.amount_style,
        uncertain=[h for h in proposal.uncertain if h not in resolved],
        headers=proposal.headers or list(headers),
        sample_rows=proposal.sample_rows or [list(r) for r in sample_rows[:10]],
    )


def template_from_mapping(
    *,
    name: str,
    headers: list[str],
    columns: dict[str, str],
    amount_style: str,
    issuer: str | None = None,
    document_type: str = "bank_statement",
    account_type: str = "bank",
    narration_dialect: str = "generic",
) -> Template:
    """Turn a confirmed mapping into a reusable template."""
    return Template(
        name=name,
        columns=columns,
        amount_style=amount_style,
        issuer=issuer,
        document_type=document_type,
        account_type=account_type,
        narration_dialect=narration_dialect,
        header_tokens=list(headers),
        source="learned",
    )
