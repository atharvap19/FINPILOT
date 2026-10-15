"""Give each segment a name a person would recognise.

The clusterer says *which* rows belong together; it cannot say what they have
in common in words. That is the one job in this pipeline a model does better
than code, and it is the only place a model is used: the ledger itself is still
produced entirely by code, and a wrong name mislabels a group without
corrupting a single figure in it.

The model sees a :class:`~segmentation.profile.SegmentProfile` and nothing
else -- statistics, never rows (see that module for what is deliberately left
out). Each segment is named in its own small call, with the names already
assigned passed along so two clusters do not both come back "everyday
spending".

Naming is best-effort by construction. If the key is missing, the model is
disabled, the call times out or the reply will not parse, :func:`rule_name`
produces a name from the same profile and the segment is marked ``rules``. A
job never fails because naming failed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from segmentation.profile import SegmentProfile

log = logging.getLogger("finpilot.segmentation.naming")

#: ``TransactionRow.segment_name`` is a 64-character column; leave headroom.
MAX_NAME_CHARS = 48


@dataclass
class SegmentName:
    segment_id: int
    name: str
    rationale: str = ""
    #: "llm" when the model named it, "rules" when the fallback did
    source: str = "rules"


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------


def describe(profile: SegmentProfile) -> str:
    """Render a profile as the compact brief the model is asked to name."""
    lines: list[str] = [
        f"size: {profile.size} transactions ({profile.share_of_rows:.1%} of the ledger)",
        (
            f"amount: median {profile.median_amount:.2f}, "
            f"typical range {profile.p25_amount:.2f}-{profile.p75_amount:.2f}, "
            f"largest {profile.max_amount:.2f}"
        ),
        f"direction: {profile.debit_share:.0%} money out",
    ]
    if profile.top_categories:
        cats = ", ".join(
            f"{c['value']} {c['share']:.0%}" for c in profile.top_categories
        )
        lines.append(f"categories from the source file: {cats}")
    if profile.top_merchants:
        named = ", ".join(
            f"{m['value']} ({m['count']})" for m in profile.top_merchants
        )
        lines.append(f"merchants: {named}")
    if profile.person_counterparty_count:
        lines.append(
            f"payments to individuals: {profile.person_counterparty_count} "
            "(names withheld)"
        )
    if profile.payment_modes:
        modes = ", ".join(f"{m['value']} {m['share']:.0%}" for m in profile.payment_modes)
        lines.append(f"payment modes: {modes}")
    if profile.channels:
        channels = ", ".join(f"{c['value']} {c['share']:.0%}" for c in profile.channels)
        lines.append(f"channels: {channels}")
    if profile.account_types:
        accounts = ", ".join(
            f"{a['value']} {a['share']:.0%}" for a in profile.account_types
        )
        lines.append(f"accounts: {accounts}")
    if profile.rows_with_time:
        lines.append(
            f"clock ({profile.rows_with_time} rows have a time): "
            f"{profile.late_night_share:.0%} late night (22:00-01:59), "
            f"{profile.business_hours_share:.0%} business hours"
        )
    lines.append(f"weekend share: {profile.weekend_share:.0%}")
    if profile.top_days_of_month:
        days = ", ".join(
            f"day {d['day']} ({d['share']:.0%})" for d in profile.top_days_of_month
        )
        lines.append(f"days of month: {days}")
    lines.append(
        f"rhythm: {profile.recurring_merchant_share:.0%} at merchants seen 3+ times, "
        f"amount variation {profile.median_amount_variation:.2f}, "
        f"interval regularity {profile.mean_interval_regularity:.2f}"
    )
    if profile.median_interval_days is not None:
        lines.append(f"median gap between rows: {profile.median_interval_days} days")
    flags = {k: v for k, v in profile.flag_shares.items() if v}
    if flags:
        lines.append(
            "flags: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(flags.items()))
        )
    return "\n".join(lines)


_INSTRUCTION = """\
You are labelling clusters of personal bank transactions for an Indian user.

Below is a statistical profile of one cluster. Give it a short, concrete name a
person would recognise on their own statement -- what this group of spending
IS, in their words.

Rules:
- at most 5 words, lowercase except proper nouns
- describe the behaviour, not the statistics: "late-night food delivery", not
  "high evening UPI cluster"
- if a rhythm is obvious (fixed amount, same day each month), say so:
  "monthly subscriptions", "salary credits", "rent payments"
- never invent a merchant, category or purpose the profile does not support
- if the profile is genuinely mixed, say so plainly: "assorted card spending"
- do not reuse any name already taken

Return JSON only: {"name": "...", "rationale": "one short sentence"}
"""


def build_prompt(profile: SegmentProfile, taken: list[str]) -> str:
    parts = [_INSTRUCTION]
    if taken:
        parts.append("Names already taken: " + ", ".join(sorted(taken)))
    parts.append(f"Cluster profile:\n{describe(profile)}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# the deterministic fallback
# --------------------------------------------------------------------------


def rule_name(profile: SegmentProfile) -> str:
    """A name derived from the profile alone, with no model involved.

    Ordered most specific first: an ingestion flag that dominates a segment is
    a far stronger statement about what it is than any amount or hour is.
    """
    flags = profile.flag_shares
    for flag, name in (
        ("is_fee", "bank fees and charges"),
        ("is_cash_withdrawal", "cash withdrawals"),
        ("is_refund", "refunds and reversals"),
        ("emi_flag", "loan EMI payments"),
        ("is_transfer", "transfers between accounts"),
    ):
        if flags.get(flag, 0.0) > 0.5:
            return name

    if profile.debit_share < 0.4:
        concentrated = profile.top_days_of_month and profile.top_days_of_month[0][
            "share"
        ] > 0.5
        return "salary and regular income" if concentrated else "incoming payments"

    steady = (
        profile.recurring_merchant_share > 0.6
        and profile.median_amount_variation < 0.15
    )
    if steady:
        return "monthly subscriptions"

    if profile.person_counterparty_count > profile.size * 0.5:
        return "payments to individuals"

    # A category the file already carried beats anything inferred from shape.
    if profile.top_categories and profile.top_categories[0]["share"] > 0.4:
        return profile.top_categories[0]["value"].lower()

    top = profile.top_merchants[0]["value"] if profile.top_merchants else ""
    if profile.late_night_share > 0.4:
        return f"late-night {top.lower()} orders" if top else "late-night spending"
    if profile.weekend_share > 0.5:
        return f"weekend {top.lower()} spending" if top else "weekend spending"
    if top:
        return f"{top.lower()} and similar"

    mode = profile.payment_modes[0]["value"].lower() if profile.payment_modes else "card"
    return f"everyday {mode} spending"


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------


def _parse(text: str) -> tuple[str, str] | None:
    """Pull ``name`` and ``rationale`` out of the reply, tolerating fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    try:
        payload = json.loads(cleaned)
    except (ValueError, IndexError):
        return None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    return name[:MAX_NAME_CHARS], str(payload.get("rationale", "")).strip()


class GeminiNamer:
    """Names segments with Gemini, one call each.

    Constructed with the key and model id rather than reading settings itself,
    so tests can drive it without touching the environment.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
        max_calls: int = 50,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_calls = max_calls
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def name_one(self, profile: SegmentProfile, taken: list[str]) -> tuple[str, str] | None:
        from google.genai import types

        client = self._ensure_client()
        response = client.models.generate_content(
            model=self.model,
            contents=build_prompt(profile, taken),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=200,
                http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000),
            ),
        )
        return _parse(response.text or "")


def name_segments(
    profiles: list[SegmentProfile], namer: GeminiNamer | None = None
) -> list[SegmentName]:
    """Name every segment, falling back to rules whenever the model cannot.

    Names are de-duplicated: a collision is suffixed rather than left ambiguous,
    because ``segment_name`` is what the UI groups by.
    """
    names: list[SegmentName] = []
    taken: list[str] = []
    calls = 0

    for profile in profiles:
        resolved: SegmentName | None = None
        if namer is not None and calls < namer.max_calls:
            calls += 1
            try:
                got = namer.name_one(profile, taken)
            except Exception as exc:  # noqa: BLE001 - naming never fails a job
                log.warning("segment %s: naming call failed (%s)", profile.segment_id, exc)
                got = None
            if got is not None:
                # Capped here as well as in _parse: this is the boundary the
                # value crosses on its way to a 64-character column, and it
                # must hold whatever produced the reply.
                resolved = SegmentName(
                    segment_id=profile.segment_id,
                    name=got[0][:MAX_NAME_CHARS],
                    rationale=got[1],
                    source="llm",
                )
        if resolved is None:
            resolved = SegmentName(
                segment_id=profile.segment_id,
                name=rule_name(profile)[:MAX_NAME_CHARS],
                rationale="named from the segment profile without a model",
                source="rules",
            )

        lowered = [t.lower() for t in taken]
        if resolved.name.lower() in lowered:
            suffix = 2
            while f"{resolved.name} {suffix}".lower() in lowered:
                suffix += 1
            resolved.name = f"{resolved.name} {suffix}"[:MAX_NAME_CHARS]
        taken.append(resolved.name)
        names.append(resolved)
    return names
