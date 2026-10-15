"""The agent loop: plan, call tools, observe, repeat, answer.

    goal ─▶ model ─┬─▶ function calls ─▶ tools (code) ─▶ results ─┐
                   │                                              │
                   └◀────────────────────────────────────────────┘
                   └─▶ no more calls ─▶ answer

Gemini chooses which tools to call and when to stop; the loop only executes
them and enforces a step budget. When the budget runs out the model is asked
once more, with tools switched off, to answer from what it has gathered -- so
a run always ends in an answer rather than a timeout.

The run is a generator of small JSON events (``thought``, ``tool_call``,
``tool_result``, ``answer``, ``error``) so the API can stream the agent's work
to the UI as it happens.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from app.agent.tools import TOOLS, AgentContext, run_tool, summarize_result
from app.config import get_settings

log = logging.getLogger("finpilot.agent")

#: Cap on how much of one tool result goes back into the conversation.
MAX_RESULT_CHARS = 24_000
MAX_HISTORY_TURNS = 10


class AgentUnavailable(RuntimeError):
    """No model configured. The agent has no rules fallback, by design."""


def make_client() -> Any:
    settings = get_settings()
    if not settings.agent_available:
        raise AgentUnavailable(
            "The query agent needs a Gemini model: set GEMINI_API_KEY (and AGENT_ENABLED=true)."
        )
    from google import genai

    return genai.Client(api_key=settings.gemini_api_key)


def _declarations() -> list[Any]:
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name=t.name, description=t.description, parameters_json_schema=t.parameters
        )
        for t in TOOLS
    ]


def _history_contents(history: list[dict[str, str]] | None) -> list[Any]:
    from google.genai import types

    out = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        text = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        out.append(
            types.Content(
                role="user" if role == "user" else "model",
                parts=[types.Part(text=text[:6000])],
            )
        )
    return out


def _generate(client: Any, model: str, contents: list[Any], config: Any) -> Any:
    """One model call, retried briefly on rate limits and overloads."""
    delays = (2.0, 6.0)
    for attempt in range(len(delays) + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            transient = any(s in text for s in ("429", "503", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"))
            if not transient or attempt == len(delays):
                raise
            log.warning("agent: transient model error (%s); retrying", text[:120])
            time.sleep(delays[attempt])
    raise RuntimeError("unreachable")


def _clip(result: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(result, default=str)
    if len(raw) <= MAX_RESULT_CHARS:
        return json.loads(raw)
    return {"truncated": True, "partial_json": raw[:MAX_RESULT_CHARS]}


def run_agent(
    job_id: str,
    *,
    system: str,
    message: str,
    history: list[dict[str, str]] | None = None,
    max_steps: int | None = None,
    client: Any = None,
    temperature: float | None = None,
    context: AgentContext | None = None,
) -> Iterator[dict[str, Any]]:
    """Run the agent to an answer, yielding events as it works."""
    from google.genai import types

    settings = get_settings()
    model = settings.gemini_agent_model
    max_steps = max_steps or settings.agent_max_steps
    ctx = context or AgentContext(job_id)
    try:
        client = client or make_client()
    except AgentUnavailable as exc:
        yield {"type": "error", "message": str(exc)}
        return

    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=_declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        temperature=settings.agent_temperature if temperature is None else temperature,
        http_options=types.HttpOptions(timeout=settings.agent_timeout_seconds * 1000),
    )
    contents = _history_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    yield {"type": "start", "model": model, "max_steps": max_steps}
    started = time.monotonic()
    call_id = 0
    tools_used: list[str] = []
    answer = ""
    tokens = {"input": 0, "output": 0, "thinking": 0}

    for step in range(1, max_steps + 2):
        final_round = step > max_steps
        if final_round:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=(
                        "You have used your tool budget. Do not call any more tools; "
                        "answer now from what you have gathered, and say what you could not check."
                    ))],
                )
            )
            config = config.model_copy(
                update={
                    "tool_config": types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(mode="NONE")
                    )
                }
            )
        try:
            response = _generate(client, model, contents, config)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent %s: model call failed", job_id)
            yield {"type": "error", "message": f"The model call failed: {type(exc).__name__}: {str(exc)[:300]}"}
            return

        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            tokens["input"] += getattr(meta, "prompt_token_count", 0) or 0
            tokens["output"] += getattr(meta, "candidates_token_count", 0) or 0
            tokens["thinking"] += getattr(meta, "thoughts_token_count", 0) or 0

        candidate = (response.candidates or [None])[0]
        parts = list(getattr(getattr(candidate, "content", None), "parts", None) or [])
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        texts: list[str] = []
        for part in parts:
            if getattr(part, "function_call", None) or not getattr(part, "text", None):
                continue
            if getattr(part, "thought", False):
                yield {"type": "thought", "text": part.text.strip()[:1500]}
            else:
                texts.append(part.text)

        if not calls or final_round:
            answer = "".join(texts).strip()
            break

        contents.append(candidate.content)
        responses = []
        for call in calls:
            call_id += 1
            args = dict(call.args or {})
            tool = next((t for t in TOOLS if t.name == call.name), None)
            label = tool.label(args) if tool else call.name
            yield {"type": "tool_call", "id": call_id, "step": step, "name": call.name, "args": args, "label": label}
            result = run_tool(ctx, call.name, args)
            tools_used.append(call.name)
            yield {"type": "tool_result", "id": call_id, "name": call.name, "summary": summarize_result(call.name, result)}
            responses.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=getattr(call, "id", None), name=call.name, response=_clip(result)
                    )
                )
            )
        contents.append(types.Content(role="user", parts=responses))

    if not answer:
        yield {"type": "error", "message": "The model returned an empty answer. Try rephrasing."}
        return

    yield {
        "type": "answer",
        "text": answer,
        # only what the answer actually cites; retrieved-but-unused passages
        # would make the source list claim support the answer does not have
        "sources": [s.as_dict() for s in ctx.sources.values() if s.filename in answer],
        "tools_used": tools_used,
        "steps": len(tools_used),
        "seconds": round(time.monotonic() - started, 1),
        "model": model,
        "tokens": tokens,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
