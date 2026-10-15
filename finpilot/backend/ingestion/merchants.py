"""Turning whatever the statement called it into one merchant name.

The same shop arrives as ``SWIGGY``, ``Swiggy In``, ``SWIGGY BANGALORE IN`` and
``swiggy.payu@hdfcbank`` depending on which file it came from, and segmentation
later needs those to be one thing. The ladder, cheapest first:

1. **clean** -- strip the noise banks add: POS prefixes, gateway suffixes,
   trailing city and country, ``PVT LTD``, reference digits.
2. **exact** -- look the cleaned name up in the dictionary of aliases.
3. **fuzzy** -- rapidfuzz against the same dictionary.
4. **embeddings** -- local semantic match (``ingestion/embeddings.py``), for
   the leftovers, in one batched pass per job.

No model is called at any tier: the ledger is produced entirely by code.
Anything still unresolved is title-cased and returned as a provisional name, so
a merchant nobody has ever seen still gets a usable label rather than a blank.
The dictionary grows: resolved names are written to the ``merchants`` table and
reused by later jobs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from ingestion.normalize import clean_text

# --------------------------------------------------------------------------
# the seed dictionary
# --------------------------------------------------------------------------

#: canonical name -> aliases as they appear in Indian statements.
#: Canonical spelling matters: ``BigBasket`` and ``Cult.fit`` are how these
#: brands are written, and title-casing the raw token would not get there.
SEED_MERCHANTS: dict[str, tuple[str, ...]] = {
    # food delivery / dining
    "Swiggy": ("swiggy", "swiggy in", "swiggy instamart", "bundl technologies", "swiggy limited"),
    "Zomato": ("zomato", "zomato ltd", "zomato limited", "zomato media"),
    "Domino's Pizza": ("dominos", "domino s pizza", "jubilant foodworks"),
    "McDonald's": ("mcdonalds", "mc donalds", "hardcastle restaurants"),
    "Starbucks": ("starbucks", "tata starbucks"),
    "Cafe Coffee Day": ("cafe coffee day", "ccd", "coffee day"),
    # groceries / retail
    "BigBasket": ("bigbasket", "big basket", "bigbasket com", "innovative retail concepts"),
    "Blinkit": ("blinkit", "grofers", "blink commerce"),
    "Zepto": ("zepto", "geddit convenience"),
    "DMart": ("dmart", "d mart", "avenue supermarts", "dmart avenue supermart", "dmart ready"),
    "Reliance Retail": ("reliance retail", "reliance fresh", "reliance smart", "relianceretail"),
    "More Supermarket": ("more supermarket", "more retail", "aditya birla retail"),
    "Spencer's": ("spencers", "spencer s retail"),
    # e-commerce
    "Amazon": ("amazon", "amazon in", "amazon india", "amazon seller services", "amazon pay"),
    "Flipkart": ("flipkart", "flipkart internet", "flipkart india"),
    "Myntra": ("myntra", "myntra designs", "myntra jabong"),
    "Ajio": ("ajio", "reliance ajio"),
    "Nykaa": ("nykaa", "fsn e commerce", "nykaa fashion"),
    "Meesho": ("meesho", "fashnear technologies"),
    # transport / fuel
    "Uber": ("uber", "uber india", "uber india systems"),
    "Ola": ("ola", "ola cabs", "ani technologies", "olacabs"),
    "Rapido": ("rapido", "roppen transportation"),
    "IRCTC": ("irctc", "irctc ltd", "indian railway catering"),
    "HPCL": ("hpcl", "hpcl petrol pump", "hindustan petroleum", "hp petrol"),
    "Indian Oil": ("indian oil", "indianoil", "iocl", "indian oil corporation"),
    "Bharat Petroleum": ("bharat petroleum", "bpcl", "bharat petrol"),
    "FASTag": ("fastag", "netc fastag", "nhai fastag"),
    # subscriptions / entertainment
    "Netflix": ("netflix", "netflix entertainment ser", "netflix entertainment services"),
    "Spotify": ("spotify", "spotify india"),
    "Amazon Prime": ("amazon prime", "prime video", "amazon prime video"),
    "Disney+ Hotstar": ("hotstar", "disney hotstar", "novi digital"),
    "YouTube Premium": ("youtube premium", "google youtube"),
    "Cult.fit": ("cultfit", "cult fit", "curefit", "cure fit healthcare", "cult pass"),
    "BookMyShow": ("bookmyshow", "book my show", "bigtree entertainment"),
    "PVR Cinemas": ("pvr", "pvr cinemas", "pvr inox"),
    # utilities / telecom
    "Airtel": ("airtel", "airtel postpaid", "bharti airtel", "airtel payments"),
    "Jio": ("jio", "reliance jio", "jio infocomm"),
    "Vodafone Idea": ("vodafone idea", "vi", "vodafone", "idea cellular"),
    "BSNL": ("bsnl", "bharat sanchar"),
    "ACT Fibernet": ("act fibernet", "act broadband", "atria convergence"),
    "Tata Power": ("tata power", "tata power ddl", "tata power company"),
    "Adani Electricity": ("adani electricity", "adani power"),
    "BESCOM": ("bescom", "bangalore electricity"),
    "MSEDCL": ("msedcl", "mahadiscom", "maharashtra state electricity"),
    # tech / cloud
    "Amazon Web Services": ("amazon web services", "aws", "aws emea", "amazon aws"),
    "Google": ("google", "google india", "google payment"),
    "Apple": ("apple", "apple services", "apple india"),
    "Microsoft": ("microsoft", "microsoft india", "msft"),
    "Samsung": ("samsung", "samsung india", "samsung india electronics"),
    "OpenAI": ("openai", "openai llc"),
    # finance
    "HDFC Bank": ("hdfc bank", "hdfc bank ltd", "hdfc"),
    "State Bank of India": ("state bank of india", "sbi", "sbi bank"),
    "ICICI Bank": ("icici bank", "icici"),
    "Axis Bank": ("axis bank", "axis"),
    "Zerodha": ("zerodha", "zerodha broking"),
    "Groww": ("groww", "nextbillion technology"),
    "LIC": ("lic", "life insurance corporation", "lic of india"),
}

#: Tokens that stay upper-case when a name is title-cased.
ACRONYMS = frozenset(
    """hdfc sbi icici axis rbl idfc hpcl bpcl iocl bsnl mtnl act pvr irctc lic upi atm
    gst tds emi aws gmbh llc llp inc uk usa us uae dmrc nhai bescom msedcl kseb tneb
    ndtv bmtc ksrtc msrtc iim iit aiims ola sbi""".split()
)

#: Lower-cased joining words inside a title-cased name.
_SMALL_WORDS = frozenset({"of", "and", "the", "in", "for", "at", "on", "de", "la"})


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------

_POS_PREFIX = re.compile(
    r"^(pos|vps|ecom|ecs|nfs|upi|imps|neft|rtgs|ach|nach|mmt|atw|nwd|inb|tpt|mps|bil)\s*[-/ ]?\s*",
    re.IGNORECASE,
)
#: The masked card number a POS narration carries between the prefix and the
#: merchant: ``POS 447712XXXXXX4417 RELIANCE RETAIL``.
_LEADING_CARD = re.compile(r"^[0-9Xx]{8,22}\s+")
_GATEWAY_SUFFIX = re.compile(
    r"\.(rzp|payu|pz|cashfree|ccav|ipay|bd|pine|yapl|mrchnt|easebuzz|billdesk|fkrt|"
    r"juspay|sbiepay|paytmqr|garage|razorpay|payswiff|fuel|store|shop)$",
    re.IGNORECASE,
)
#: Only *legal form* suffixes are stripped. Words like ``Technologies`` or
#: ``Services`` are usually part of the name itself -- stripping them turns
#: ``Acme Technologies Pvt Ltd`` into ``Acme``, which is a different company.
_CORP_SUFFIX = re.compile(
    r"\b(pvt|private|ltd|limited|llp|inc|incorporated|corp|corporation|co|company|"
    r"india|indias|in|intl|international)\b\.?",
    re.IGNORECASE,
)

#: Aliases shorter than this are never fuzzy-matched. ``vi`` (Vodafone Idea) is
#: a real alias but would otherwise match half the names in the country.
_MIN_FUZZY_ALIAS = 4
#: A candidate and an alias whose lengths differ by more than this ratio are
#: not the same merchant, whatever the character overlap says.
_MIN_LENGTH_RATIO = 0.6
_TRAILING_IDS = re.compile(r"[\s\-_/]*\b[0-9]{4,}\b\s*$")
_PUNCT = re.compile(r"[^\w&.'\- ]+")
_MULTI_SPACE = re.compile(r"\s+")


def clean_merchant_name(raw: str | None, *, drop_corp_suffix: bool = True) -> str:
    """Strip the noise banks wrap around a merchant name.

    ``drop_corp_suffix=False`` keeps ``Pvt Ltd`` etc., which matters when the
    suffix is the only thing distinguishing two names.
    """
    text = clean_text(raw)
    if not text:
        return ""
    if "@" in text:
        text = text.split("@")[0]
        text = _GATEWAY_SUFFIX.sub("", text)
        text = text.replace(".", " ")
    text = _POS_PREFIX.sub("", text)
    text = _LEADING_CARD.sub("", text)
    text = _TRAILING_IDS.sub("", text)
    text = _PUNCT.sub(" ", text)
    if drop_corp_suffix:
        stripped = _CORP_SUFFIX.sub(" ", text)
        # never strip a name down to nothing
        if stripped.strip():
            text = stripped
    text = _MULTI_SPACE.sub(" ", text).strip(" -.")
    return text


def smart_title(text: str) -> str:
    """Title-case that leaves acronyms alone: ``HDFC BANK`` -> ``HDFC Bank``."""
    words = clean_text(text).split()
    out: list[str] = []
    for i, word in enumerate(words):
        low = word.lower().strip(".")
        if low in ACRONYMS:
            out.append(word.upper())
        elif i > 0 and low in _SMALL_WORDS:
            out.append(low)
        elif any(ch.isdigit() for ch in word):
            out.append(word.upper())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def _key(text: str) -> str:
    return _MULTI_SPACE.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


# --------------------------------------------------------------------------
# the resolver
# --------------------------------------------------------------------------


@dataclass
class MerchantMatch:
    canonical: str | None
    confidence: float
    method: str  # exact | fuzzy | embedding | person | cleaned | none
    alias: str | None = None


@dataclass
class MerchantResolver:
    """Dictionary-backed merchant resolution.

    Callers add learned names with :meth:`learn` so the next file gets the
    cheap path.
    """

    fuzzy_threshold: int = 88
    #: alias key -> canonical name
    _index: dict[str, str] = field(default_factory=dict)
    _canonical: set[str] = field(default_factory=set)
    _cache: dict[str, MerchantMatch] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._index:
            for canonical, aliases in SEED_MERCHANTS.items():
                self.learn(canonical, aliases)

    # -- dictionary ------------------------------------------------------
    def learn(self, canonical: str, aliases: tuple[str, ...] | list[str] = ()) -> None:
        canonical = clean_text(canonical)
        if not canonical:
            return
        self._canonical.add(canonical)
        self._index[_key(canonical)] = canonical
        self._index[_key(clean_merchant_name(canonical))] = canonical
        for alias in aliases:
            key = _key(alias)
            if key:
                self._index[key] = canonical
        self._cache.clear()

    @property
    def canonical_names(self) -> list[str]:
        return sorted(self._canonical)

    def aliases_for(self, canonical: str) -> list[str]:
        return sorted(a for a, c in self._index.items() if c == canonical)

    def is_known(self, name: str | None) -> bool:
        """Used by narration parsing to tell a shop from a person."""
        if not name:
            return False
        match = self.resolve(name)
        return match.method in ("exact", "fuzzy") and match.confidence >= 0.85

    # -- resolution ------------------------------------------------------
    def resolve(
        self, raw: str | None, *, vpa: str | None = None, is_person: bool = False
    ) -> MerchantMatch:
        """Best canonical name for one raw counterparty string.

        ``is_person=True`` skips the dictionary entirely: a person's name must
        never be dragged onto a brand by a fuzzy match, and tidying the casing
        is all that is wanted.
        """
        if is_person:
            cleaned = clean_merchant_name(raw, drop_corp_suffix=False)
            return MerchantMatch(smart_title(cleaned) or None, 0.9 if cleaned else 0.0,
                                 "person", None)
        cache_key = f"{raw}|{vpa}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        match = self._resolve_uncached(raw, vpa)
        self._cache[cache_key] = match
        return match

    def _resolve_uncached(self, raw: str | None, vpa: str | None) -> MerchantMatch:
        # the whole VPA, not just its local part: cleaning needs the ``@`` to
        # know it is looking at a VPA and strip the gateway suffix
        candidates = [raw]
        if vpa:
            candidates.append(vpa)
        for candidate in candidates:
            text = clean_text(candidate)
            if not text:
                continue
            # try the untouched name first: "Amazon Web Services" must not be
            # reduced to "Amazon" by suffix-stripping before it is looked up
            for probe in (text, clean_merchant_name(text, drop_corp_suffix=False),
                          clean_merchant_name(text)):
                key = _key(probe)
                if key and key in self._index:
                    return MerchantMatch(self._index[key], 1.0, "exact", probe)

        for candidate in candidates:
            cleaned = _key(clean_merchant_name(candidate))
            if len(cleaned) < _MIN_FUZZY_ALIAS:
                continue
            pool = [
                a
                for a in self._index
                if len(a) >= _MIN_FUZZY_ALIAS
                and min(len(a), len(cleaned)) / max(len(a), len(cleaned)) >= _MIN_LENGTH_RATIO
            ]
            best = process.extractOne(
                cleaned, pool, scorer=fuzz.WRatio, score_cutoff=self.fuzzy_threshold
            )
            if best:
                alias, score, _ = best
                return MerchantMatch(self._index[alias], score / 100.0, "fuzzy", alias)

        cleaned = clean_merchant_name(raw)
        if cleaned:
            return MerchantMatch(smart_title(cleaned), 0.55, "cleaned", None)
        return MerchantMatch(None, 0.0, "none", None)


    # -- the expensive tiers, run once per job over the leftovers ---------
    def unresolved(self, names: list[str | None]) -> list[str]:
        """Names the cheap tiers could only tidy up, not recognise.

        Kept separate from :meth:`resolve` on purpose: resolve runs per row and
        must stay free, while these tiers cost a model load or a network call
        and are worth paying once for the distinct leftovers.
        """
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            if self.resolve(name).method == "cleaned":
                out.append(name)
        return out

    def refine(
        self,
        names: list[str | None],
        *,
        embedding_threshold: float = 0.80,
    ) -> dict[str, str]:
        """Resolve leftovers by local embedding similarity.

        Whatever is resolved is learned, so the next file of the same shape
        takes the cheap path. Returns ``{raw name: canonical}`` for what
        actually changed; a name nothing recognises keeps its cleaned form,
        which is a usable label rather than a blank.
        """
        leftovers = self.unresolved(names)
        if not leftovers:
            return {}

        resolved: dict[str, str] = {}
        remaining = list(leftovers)

        from ingestion import embeddings as emb

        if emb.available():
            index = emb.MerchantEmbeddingIndex()
            if index.build(self.canonical_names):
                still: list[str] = []
                for raw in remaining:
                    hit = index.best(clean_merchant_name(raw), threshold=embedding_threshold)
                    if hit is None:
                        still.append(raw)
                        continue
                    resolved[raw] = hit.candidate
                    self.learn(hit.candidate, (raw,))
                remaining = still

        self._cache.clear()
        return resolved


_DEFAULT: MerchantResolver | None = None


def default_resolver() -> MerchantResolver:
    """Process-wide resolver seeded from :data:`SEED_MERCHANTS`."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = MerchantResolver()
    return _DEFAULT


def reset_default_resolver() -> None:
    global _DEFAULT
    _DEFAULT = None
