"""Planner Agent — turns a research question into an executable research plan."""

import datetime as _dt
import logging
from typing import Any, Dict

from app.config import settings
from app.core.llm import LLMError, chat_json

log = logging.getLogger("verinexus.planner")

SYSTEM = """You are the Planner Agent of an evidence-based research system.

Break a research question into a small, non-overlapping plan. Reply with JSON only.

Rules:
- 2 to {max_sub} sub-questions. Each must be independently answerable and must NOT
  restate the others. If the question is simple, return 2.
- Search queries are keyword-style strings for a web search engine, not sentences.
  Write 1 or 2 per sub-question. Include distinguishing entities, never pronouns.
- Set needs_recency true only if the correct answer could have changed in the last
  18 months (prices, office-holders, versions, ongoing events, current statistics).
- Set contested true if reasonable, well-informed sources are likely to disagree.
  This tells the verifier to expect and preserve conflict rather than resolve it.

JSON schema:
{{
  "intent": "one short sentence naming what the user actually wants",
  "needs_recency": true,
  "contested": false,
  "sub_questions": [
    {{"id": "q1", "question": "...", "search_queries": ["...", "..."]}}
  ]
}}"""

USER = """Today's date: {today}

Research question:
{query}

Return the JSON plan."""


def _fallback(query: str) -> Dict[str, Any]:
    return {
        "intent": query[:160],
        "needs_recency": False,
        "contested": False,
        "sub_questions": [
            {"id": "q1", "question": query, "search_queries": [query[:300]]},
        ],
        "degraded": True,
    }


def plan(query: str) -> Dict[str, Any]:
    today = _dt.date.today().isoformat()
    try:
        out = chat_json(
            [
                {"role": "system", "content": SYSTEM.format(max_sub=settings.MAX_SUB_QUESTIONS)},
                {"role": "user", "content": USER.format(today=today, query=query)},
            ],
            temperature=0.15,
            max_tokens=900,
        )
    except LLMError as exc:
        log.warning("planner failed, using fallback: %s", exc)
        return _fallback(query)

    subs = out.get("sub_questions") or []
    cleaned = []
    for i, s in enumerate(subs[: settings.MAX_SUB_QUESTIONS], start=1):
        q = (s.get("question") or "").strip()
        if not q:
            continue
        queries = [x.strip() for x in (s.get("search_queries") or []) if x and x.strip()]
        cleaned.append(
            {"id": s.get("id") or f"q{i}", "question": q, "search_queries": queries[:2] or [q]}
        )

    if not cleaned:
        return _fallback(query)

    if out.get("needs_recency"):
        year = _dt.date.today().year
        for s in cleaned:
            s["search_queries"] = [f"{sq} {year}" for sq in s["search_queries"]]

    return {
        "intent": (out.get("intent") or query)[:300],
        "needs_recency": bool(out.get("needs_recency")),
        "contested": bool(out.get("contested")),
        "sub_questions": cleaned,
        "degraded": False,
    }


def all_search_queries(plan_obj: Dict[str, Any]) -> list:
    seen, out = set(), []
    for s in plan_obj.get("sub_questions", []):
        for q in s.get("search_queries", []):
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(q)
    return out
