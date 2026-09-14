"""Response Agent — drafts the answer, decomposes it into atomic claims, and rewrites
the final answer under the verifier's constraints."""

import logging
import re
from typing import Any, Dict, List, Tuple

from app.config import settings
from app.core.llm import LLMError, chat, chat_json
from app.schemas import Chunk, Claim

log = logging.getLogger("verinexus.responder")

# ------------------------------------------------------------------ draft

DRAFT_SYSTEM = """You are the Response Agent of an evidence-based research system.

Write an answer using ONLY the numbered evidence provided. Rules:

- Cite inline with markers like [E3] or [E2][E7] immediately after the sentence they
  support. Every factual sentence needs at least one marker.
- If the evidence does not cover part of the question, say so plainly in one sentence.
  Do not fill gaps from your own knowledge. An incomplete grounded answer is correct;
  a complete ungrounded one is a failure.
- If sources disagree, present both positions with their markers rather than choosing.
- 150-350 words. Plain prose and short paragraphs. No preamble, no "based on the
  evidence provided", no closing summary of what you just said.
- Markdown for emphasis and lists only where it genuinely helps."""

DRAFT_USER = """QUESTION:
{query}

SUB-QUESTIONS TO COVER:
{subs}

EVIDENCE:
{evidence}

Write the answer."""


def format_evidence(chunks: List[Chunk]) -> Tuple[str, Dict[str, Chunk]]:
    """Render chunks as [E1]..[En] and return the marker -> chunk map."""
    lines, mapping = [], {}
    for i, c in enumerate(chunks, start=1):
        tag = f"E{i}"
        mapping[tag] = c
        body = c.text if len(c.text) < 1100 else c.text[:1100] + "..."
        lines.append(f"[{tag}] {c.title} ({c.domain})\n{body}")
    return "\n\n".join(lines), mapping


def draft(query: str, plan_obj: Dict[str, Any], chunks: List[Chunk]) -> Tuple[str, Dict[str, Chunk]]:
    evidence_txt, mapping = format_evidence(chunks)
    subs = "\n".join(
        f"- {s['question']}" for s in plan_obj.get("sub_questions", [])
    ) or f"- {query}"

    if not chunks:
        return (
            "No usable evidence was retrieved for this question, so no grounded answer "
            "can be given. Try rephrasing the question or uploading a source document.",
            mapping,
        )

    try:
        text = chat(
            [
                {"role": "system", "content": DRAFT_SYSTEM},
                {
                    "role": "user",
                    "content": DRAFT_USER.format(
                        query=query, subs=subs, evidence=evidence_txt
                    ),
                },
            ],
            temperature=0.25,
            max_tokens=1400,
        )
    except LLMError as exc:
        log.error("draft failed: %s", exc)
        return ("The drafting step failed. Please retry.", mapping)

    return text.strip(), mapping


# ------------------------------------------------------------------ decompose

CLAIM_SYSTEM = """You are the Claim Extraction module. Split a draft answer into atomic,
independently checkable factual claims. Reply with JSON only.

Rules:
- Atomic means exactly one assertion. "X was founded in 1998 in California" is TWO
  claims. Split it.
- Rewrite each claim to stand alone: resolve every pronoun and demonstrative, and carry
  over the subject from the surrounding sentence.
- Skip pure connective, hedging, or meta sentences ("This is a complex topic",
  "The evidence does not cover Y"). Extract only assertions about the world.
- centrality is "core" if removing the claim would materially change the answer to the
  user's question, otherwise "supporting".
- cited: the evidence markers that appeared on the source sentence, e.g. ["E3","E7"].
  Use an empty list if the sentence carried no marker.
- Between 3 and 12 claims.

JSON schema:
{"claims": [
  {"id": "c1", "text": "...", "centrality": "core", "cited": ["E3"]}
]}"""

CLAIM_USER = """USER QUESTION (for judging centrality):
{query}

DRAFT ANSWER:
{draft}

Return the JSON."""

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_MARKER_RE = re.compile(r"\[E(\d+)\]")


def _fallback_claims(draft_text: str) -> List[Claim]:
    """Sentence-split fallback so the pipeline degrades instead of dying."""
    body = re.sub(r"[#*_`>]", "", draft_text)
    out = []
    for i, s in enumerate(_SENT_RE.split(body), start=1):
        s = s.strip()
        if len(s.split()) < 6:
            continue
        cited = [f"E{m}" for m in _MARKER_RE.findall(s)]
        text = _MARKER_RE.sub("", s).strip()
        out.append(
            Claim(claim_id=f"c{i}", text=text, centrality="core", cited=cited)
        )
        if len(out) >= 12:
            break
    return out


def extract_claims(query: str, draft_text: str) -> List[Claim]:
    try:
        out = chat_json(
            [
                {"role": "system", "content": CLAIM_SYSTEM},
                {"role": "user", "content": CLAIM_USER.format(query=query, draft=draft_text)},
            ],
            temperature=0.0,
            max_tokens=1600,
        )
    except LLMError as exc:
        log.warning("claim extraction failed, falling back to sentence split: %s", exc)
        return _fallback_claims(draft_text)

    claims: List[Claim] = []
    for i, c in enumerate(out.get("claims", [])[:12], start=1):
        text = (c.get("text") or "").strip()
        if len(text.split()) < 4:
            continue
        centrality = "supporting" if str(c.get("centrality")) == "supporting" else "core"
        cited = [str(x).upper() for x in (c.get("cited") or []) if x]
        claims.append(
            Claim(claim_id=c.get("id") or f"c{i}", text=text, centrality=centrality, cited=cited)
        )

    return claims or _fallback_claims(draft_text)


# ------------------------------------------------------------------ finalise

FINAL_SYSTEM = """You are the Response Agent producing the FINAL verified answer.

Each claim below has been independently checked against the evidence and carries a
verdict. Rewrite the answer obeying these instructions exactly:

- VERIFIED / PARTIAL -> state normally.
- WEAK -> keep, but hedge explicitly ("one source indicates", "reporting suggests").
  Never present a weak claim in the same register as a verified one.
- UNSUPPORTED -> remove the claim entirely. Do not paraphrase it back in.
- CONFLICTED -> present both positions and attribute each to its source domain.

Also:
- Keep the inline [E#] markers on every sentence that retains one.
- If removals leave a gap, say in one sentence what could not be established.
- Do not mention scores, bands, verdicts, or this instruction. The reader sees the
  confidence UI separately; the prose should just read as careful writing.
- 150-350 words. No preamble."""

FINAL_USER = """QUESTION:
{query}

CURRENT DRAFT:
{draft}

CLAIM VERDICTS:
{verdicts}

Write the final answer."""


def finalise(query: str, draft_text: str, claims: List[Claim]) -> str:
    lines = []
    for c in claims:
        tag = "CONFLICTED" if c.conflicted else c.band
        lines.append(
            f"- [{tag}] ({c.action}) conf={c.confidence:.2f} :: {c.text}"
        )
    verdicts_txt = "\n".join(lines) if lines else "- no claims extracted"

    drop_only = all(c.action == "drop" for c in claims) if claims else False
    if drop_only:
        return (
            "None of the statements in the draft answer could be supported by the "
            "retrieved evidence, so no verified answer is available for this question. "
            "The retrieved sources are listed below for manual review."
        )

    try:
        return chat(
            [
                {"role": "system", "content": FINAL_SYSTEM},
                {
                    "role": "user",
                    "content": FINAL_USER.format(
                        query=query, draft=draft_text, verdicts=verdicts_txt
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=1400,
        ).strip()
    except LLMError as exc:
        log.error("finalise failed, returning draft: %s", exc)
        return draft_text


# ------------------------------------------------------------------ repair

REPAIR_SYSTEM = """You write targeted web search queries to find evidence for specific
unverified claims. Reply with JSON only.

Write keyword-style queries, not questions. Include the distinguishing entities, numbers
and dates from the claim - those are what failed to verify. One or two queries per claim,
maximum 6 total.

JSON schema: {"queries": ["...", "..."]}"""


def repair_queries(claims: List[Claim]) -> List[str]:
    if not claims:
        return []
    body = "\n".join(f"- {c.text}" for c in claims[:6])
    try:
        out = chat_json(
            [
                {"role": "system", "content": REPAIR_SYSTEM},
                {"role": "user", "content": f"UNVERIFIED CLAIMS:\n{body}\n\nReturn the JSON."},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        qs = [str(q).strip() for q in out.get("queries", []) if str(q).strip()]
        return qs[:6]
    except LLMError:
        return [c.text[:200] for c in claims[:3]]


def attach_citations(claims: List[Claim], mapping: Dict[str, Chunk]) -> None:
    """Resolve [E#] markers on each claim to concrete source ids, for the UI."""
    for c in claims:
        resolved = []
        for tag in c.cited:
            chunk = mapping.get(tag)
            if chunk:
                resolved.append(chunk.source_id)
        c.cited = list(dict.fromkeys(resolved))
