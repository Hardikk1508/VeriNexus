"""Verification Agent — grounds each atomic claim against the retrieved evidence pool.

Two interchangeable NLI backends:
  - "llm"   Groq llama-3.3-70b as a constrained judge (default; no model download)
  - "local" MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli cross-encoder

Direction matters: premise = evidence, hypothesis = claim. Reversing these is the
most common bug in claim-verification systems and silently inflates support scores.
"""

import logging
import threading
from typing import Dict, List, Optional

from app.config import settings
from app.core.llm import LLMError, chat_json
from app.core.retrieval import EvidenceStore
from app.core.scoring import score_claim
from app.schemas import Chunk, Claim, EvidenceVerdict

log = logging.getLogger("verinexus.verifier")

SYSTEM = """You are the Verification Agent. You judge whether each piece of evidence
supports a claim. You are deliberately strict. Reply with JSON only.

For every evidence item, choose exactly one label:
- "entail"     : the evidence states or directly implies the claim. A claim that is
                 more specific than the evidence (extra numbers, dates, causes, or
                 superlatives not present in the evidence) is NOT entailed.
- "contradict" : the evidence asserts something incompatible with the claim.
- "neutral"    : the evidence is about the topic but does not settle the claim.

"Related to the same subject" is neutral, not entailment. Absence of evidence is
neutral, never contradiction.

confidence is your certainty in the label, 0.0 to 1.0. Use values below 0.7 freely
when the evidence is partial or the wording is ambiguous.

JSON schema:
{"judgements": [
  {"evidence_id": "E1", "label": "entail", "confidence": 0.86, "reason": "max 15 words"}
]}"""

USER = """CLAIM:
{claim}

EVIDENCE:
{evidence}

Judge every evidence item. Return the JSON."""


# ------------------------------------------------------------------ local NLI

_local = None
_local_lock = threading.Lock()


def _load_local():
    global _local
    if _local is None:
        with _local_lock:
            if _local is None:
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                log.info("loading local NLI model %s", settings.LOCAL_NLI_MODEL)
                # use_fast=False: the fast/tiktoken tokenizer converter in current
                # `transformers` chokes on this checkpoint's sentencepiece model file.
                tok = AutoTokenizer.from_pretrained(settings.LOCAL_NLI_MODEL, use_fast=False)
                mdl = AutoModelForSequenceClassification.from_pretrained(
                    settings.LOCAL_NLI_MODEL
                )
                mdl.eval()
                _local = (tok, mdl)
    return _local


def _nli_local(claim: str, chunks: List[Chunk]) -> Dict[str, Dict[str, float]]:
    import torch

    tok, mdl = _load_local()
    premises = [c.text for c in chunks]
    hypotheses = [claim] * len(chunks)
    with torch.no_grad():
        enc = tok(
            premises, hypotheses, return_tensors="pt", truncation=True,
            padding=True, max_length=512,
        )
        probs = torch.softmax(mdl(**enc).logits, dim=-1).tolist()

    # label order for this checkpoint: entailment, neutral, contradiction
    id2label = {int(k): v.lower() for k, v in mdl.config.id2label.items()}
    out = {}
    for c, row in zip(chunks, probs):
        mapped = {"entail": 0.0, "neutral": 0.0, "contradict": 0.0}
        for idx, p in enumerate(row):
            name = id2label.get(idx, "")
            if name.startswith("entail"):
                mapped["entail"] = p
            elif name.startswith("contradic"):
                mapped["contradict"] = p
            else:
                mapped["neutral"] = p
        out[c.chunk_id] = mapped
    return out


# ------------------------------------------------------------------ LLM NLI


def _nli_llm(claim: str, chunks: List[Chunk]) -> Dict[str, Dict[str, float]]:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        body = c.text if len(c.text) < 1400 else c.text[:1400] + "..."
        blocks.append(f"[E{i}] (source: {c.domain})\n{body}")
    evidence_txt = "\n\n".join(blocks)

    try:
        out = chat_json(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(claim=claim, evidence=evidence_txt)},
            ],
            temperature=0.0,
            max_tokens=1200,
        )
    except LLMError as exc:
        log.warning("NLI call failed for claim %r: %s", claim[:60], exc)
        return {c.chunk_id: {"entail": 0.0, "neutral": 1.0, "contradict": 0.0} for c in chunks}

    by_index: Dict[int, Dict] = {}
    for j in out.get("judgements", []):
        raw_id = str(j.get("evidence_id", "")).upper().lstrip("E")
        try:
            by_index[int(raw_id)] = j
        except ValueError:
            continue

    result = {}
    for i, c in enumerate(chunks, start=1):
        j = by_index.get(i)
        if not j:
            result[c.chunk_id] = {"entail": 0.0, "neutral": 1.0, "contradict": 0.0}
            continue
        label = str(j.get("label", "neutral")).lower()
        try:
            conf = float(j.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        rest = (1.0 - conf) / 2.0
        if label.startswith("entail"):
            probs = {"entail": conf, "neutral": rest, "contradict": rest}
        elif label.startswith("contradic"):
            probs = {"entail": rest, "neutral": rest, "contradict": conf}
        else:
            probs = {"entail": rest, "neutral": conf, "contradict": rest}
        probs["_reason"] = (j.get("reason") or "")[:160]
        result[c.chunk_id] = probs
    return result


def _nli(claim: str, chunks: List[Chunk]) -> Dict[str, Dict]:
    if not chunks:
        return {}
    if settings.NLI_BACKEND == "local":
        try:
            return _nli_local(claim, chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("local NLI unavailable (%s); falling back to LLM judge", exc)
    return _nli_llm(claim, chunks)


# ------------------------------------------------------------------ public


def verify_claim(claim: Claim, store: EvidenceStore, k: Optional[int] = None) -> Claim:
    """Retrieve evidence for one claim, run NLI, and compute its CEVS score."""
    k = k or settings.EVIDENCE_PER_CLAIM
    chunks = store.search_diverse(claim.text, k=k, per_domain=2)

    if not chunks:
        claim.verdicts = []
        return score_claim(claim)

    probs = _nli(claim.text, chunks)
    from app.core.scoring import source_authority

    verdicts = []
    for c in chunks:
        p = probs.get(c.chunk_id, {"entail": 0.0, "neutral": 1.0, "contradict": 0.0})
        snippet = c.text[:260] + ("..." if len(c.text) > 260 else "")
        verdicts.append(
            EvidenceVerdict(
                chunk_id=c.chunk_id,
                source_id=c.source_id,
                domain=c.domain,
                snippet=snippet,
                authority=source_authority(c.domain, c.kind),
                p_entail=float(p.get("entail", 0.0)),
                p_contradict=float(p.get("contradict", 0.0)),
                p_neutral=float(p.get("neutral", 0.0)),
                rationale=str(p.get("_reason", "")),
            )
        )

    claim.verdicts = verdicts
    return score_claim(claim)


def verify_all(claims: List[Claim], store: EvidenceStore) -> List[Claim]:
    return [verify_claim(c, store) for c in claims]
