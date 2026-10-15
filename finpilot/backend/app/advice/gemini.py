"""Ask Gemini to write advice from the brief. Never from raw rows."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.advice.brief import AdviceBrief

log = logging.getLogger("finpilot.advice")

_INSTRUCTION = """\
You are FinPilot, advising one Indian user from their own bank ledger.

You are given a statistical brief built from documents they uploaded, plus
optional spending segments (habits found by clustering). Answer their question
using only that brief.

Rules:
- Never invent a merchant, segment, amount, bill or month that is not in the brief.
- Prefer the segments when they exist: they are habits, not categories guessed from text.
- Give practical next-month actions. Quote rupee figures from the brief.
- Do not ask for account numbers, passwords, OTPs or raw statements.
- You are not a licensed advisor; do not give tax, investment or legal advice.
- If segmentation has not been run, say so and still answer from monthly totals.
- Keep the answer to a few short paragraphs.

Return JSON only:
{
  "answer": "markdown-free prose",
  "recommendations": [
    {
      "title": "short action",
      "detail": "why this, from the numbers",
      "monthly_saving_rupees": 0,
      "segment_id": null,
      "segment_name": null
    }
  ]
}
monthly_saving_rupees may be null when the action is not a cut.
Use at most 5 recommendations. segment_id must match the brief or be null.
"""


def build_prompt(
    brief: AdviceBrief,
    question: str,
    history: list[dict[str, str]],
) -> str:
    parts = [_INSTRUCTION, "Brief:\n" + brief.as_prompt()]
    if history:
        prior = "\n".join(
            f"{turn['role']}: {turn['content']}" for turn in history[-6:]
        )
        parts.append("Earlier in this chat:\n" + prior)
    parts.append("User question:\n" + question.strip())
    return "\n\n".join(parts)


def _parse(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        cleaned = cleaned.removeprefix("json").strip()
    try:
        payload = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    answer = str(payload.get("answer", "")).strip()
    if not answer:
        return None
    recs_in = payload.get("recommendations") or []
    recs: list[dict[str, Any]] = []
    if isinstance(recs_in, list):
        for item in recs_in[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            saving = item.get("monthly_saving_rupees")
            try:
                saving_n = float(saving) if saving is not None else None
            except (TypeError, ValueError):
                saving_n = None
            segment_id = item.get("segment_id")
            try:
                segment_id_n = int(segment_id) if segment_id is not None else None
            except (TypeError, ValueError):
                segment_id_n = None
            recs.append(
                {
                    "title": title[:120],
                    "detail": str(item.get("detail", "")).strip()[:800],
                    "monthly_saving_rupees": saving_n,
                    "segment_id": segment_id_n,
                    "segment_name": (
                        str(item["segment_name"]).strip()[:64]
                        if item.get("segment_name")
                        else None
                    ),
                }
            )
    return {"answer": answer[:4000], "recommendations": recs}


class GeminiAdvisor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def advise(
        self,
        brief: AdviceBrief,
        question: str,
        history: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        from google.genai import types

        client = self._ensure_client()
        response = client.models.generate_content(
            model=self.model,
            contents=build_prompt(brief, question, history),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=1200,
                http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000),
            ),
        )
        return _parse(response.text or "")
