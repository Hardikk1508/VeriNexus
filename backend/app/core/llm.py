"""Groq chat wrapper: retry with jittered backoff, model fallback, strict JSON parsing."""

import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional

from groq import Groq

from app.config import settings

log = logging.getLogger("verinexus.llm")

_client: Optional[Groq] = None


def client() -> Groq:
    global _client
    if _client is None:
        if not settings.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set. Copy .env.example to .env.")
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


class LLMError(RuntimeError):
    pass


_RETRYABLE = ("rate", "429", "500", "502", "503", "504", "timeout", "overload", "connection")


def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = False,
    retries: int = 4,
) -> str:
    """Single completion. Falls back to the fast model on the final attempt."""
    primary = model or settings.GROQ_MODEL
    last_err: Optional[Exception] = None

    for attempt in range(retries):
        use_model = primary if attempt < retries - 1 else settings.GROQ_FAST_MODEL
        kwargs: Dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client().chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            msg = str(exc).lower()
            if not any(t in msg for t in _RETRYABLE) and attempt >= 1:
                break
            if attempt == retries - 1:
                break
            delay = min(1.5 * (2**attempt) + random.random(), 20.0)
            log.warning("groq retry %s/%s in %.1fs: %s", attempt + 1, retries, delay, exc)
            time.sleep(delay)

    raise LLMError(f"Groq call failed after {retries} attempts: {last_err}")


_OBJ_RE = re.compile(r"\{.*\}", re.S)


def _coerce_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _OBJ_RE.search(raw)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # trailing-comma repair, the single most common LLM JSON defect
            repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Could not parse JSON from model output: {raw[:300]}")


def chat_json(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    retries: int = 4,
) -> Dict[str, Any]:
    """Completion constrained to a JSON *object*.

    Groq's json_object mode rejects top-level arrays, so every prompt in this project
    asks for an object wrapper (e.g. {"items": [...]}).
    """
    raw = chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
        retries=retries,
    )
    out = _coerce_json(raw)
    if not isinstance(out, dict):
        raise LLMError("Expected a JSON object at the top level.")
    return out
