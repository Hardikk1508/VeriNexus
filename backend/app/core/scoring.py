"""CEVS — Claim-Evidence Verification & Scoring.

Pure functions, no I/O, no LLM calls. This module is the formal core of VeriNexus and
is directly unit-testable; see docs/ALGORITHM.md sections 4-6 for the derivation.
"""

from typing import Dict, List, Tuple
from urllib.parse import urlparse

from app.config import settings
from app.schemas import Claim, EvidenceVerdict

# ------------------------------------------------------------------ authority

_AUTHORITY_TIERS: List[Tuple[float, Tuple[str, ...]]] = [
    (
        0.95,
        (
            "arxiv.org", "nature.com", "science.org", "sciencedirect.com", "springer.com",
            "ieee.org", "acm.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
            "nih.gov", "who.int", "jstor.org", "plos.org", "cell.com", "bmj.com",
            "thelancet.com", "aclanthology.org", "openreview.net",
        ),
    ),
    (
        0.88,
        (
            ".gov", ".gov.in", ".gov.uk", ".edu", ".ac.in", ".ac.uk",
            "europa.eu", "un.org", "worldbank.org", "oecd.org", "imf.org",
            "rbi.org.in", "niti.gov.in",
        ),
    ),
    (
        0.75,
        (
            "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "ft.com",
            "economist.com", "nytimes.com", "wsj.com", "thehindu.com",
            "indianexpress.com", "britannica.com", "nasa.gov", "esa.int",
        ),
    ),
    (
        0.65,
        (
            "wikipedia.org", "stanford.edu", "mit.edu", "docs.python.org",
            "developer.mozilla.org", "ietf.org", "w3.org", "iso.org",
        ),
    ),
    (
        0.55,
        (
            "github.com", "stackoverflow.com", "arxiv-sanity.com",
            "towardsdatascience.com", "distill.pub",
        ),
    ),
]

_LOW_TRUST = (
    "medium.com", "quora.com", "reddit.com", "blogspot.", "wordpress.com",
    "substack.com", "pinterest.", "facebook.com", "x.com", "twitter.com",
)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return "unknown"
    return host[4:] if host.startswith("www.") else (host or "unknown")


def source_authority(domain: str, kind: str = "web") -> float:
    """Heuristic source-quality prior in [0, 1].

    Uploaded documents get 0.80: the user explicitly vouched for them, but they are
    still not peer-reviewed literature.
    """
    if kind == "upload":
        return 0.80
    d = (domain or "").lower()
    if not d or d == "unknown":
        return 0.35
    for score, patterns in _AUTHORITY_TIERS:
        if any(p in d for p in patterns):
            return score
    if any(p in d for p in _LOW_TRUST):
        return 0.35
    return 0.50


# ------------------------------------------------------------------ signals


def support_score(verdicts: List[EvidenceVerdict]) -> float:
    """S(c) — strongest single entailment. One good witness is enough to start."""
    if not verdicts:
        return 0.0
    return max(v.p_entail for v in verdicts)


def agreement_score(verdicts: List[EvidenceVerdict], theta: float) -> float:
    """A(c) — fraction of *distinct domains* that entail the claim.

    Counted over domains, not chunks: three paragraphs of one article are one witness.
    """
    if not verdicts:
        return 0.0
    all_domains = {v.domain for v in verdicts}
    supporting = {v.domain for v in verdicts if v.p_entail > theta}
    return len(supporting) / max(len(all_domains), 1)


def authority_score(verdicts: List[EvidenceVerdict], theta: float) -> float:
    """Q(c) — mean authority of the *supporting* evidence only."""
    supporters = [v.authority for v in verdicts if v.p_entail > theta]
    if not supporters:
        return 0.0
    return sum(supporters) / len(supporters)


def contradiction_score(verdicts: List[EvidenceVerdict]) -> float:
    """X(c) — strongest opposing evidence anywhere in the retrieved set."""
    if not verdicts:
        return 0.0
    return max(v.p_contradict for v in verdicts)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def claim_confidence(s: float, a: float, q: float, x: float) -> float:
    """conf(c) = clamp01(w_s*S + w_a*A + w_q*Q - w_x*X)

    Weights sum to 0.85 on the positive side by design: a single perfectly-entailing
    source with no corroboration must not reach certainty.
    """
    return _clamp01(
        settings.W_SUPPORT * s
        + settings.W_AGREEMENT * a
        + settings.W_AUTHORITY * q
        - settings.W_CONTRADICTION * x
    )


def verdict_band(conf: float) -> str:
    if conf >= settings.BAND_VERIFIED:
        return "VERIFIED"
    if conf >= settings.BAND_PARTIAL:
        return "PARTIAL"
    if conf >= settings.BAND_WEAK:
        return "WEAK"
    return "UNSUPPORTED"


def is_conflicted(s: float, x: float) -> bool:
    """Strong evidence on both sides — surface it, never silently pick a winner."""
    return x >= settings.CONFLICT_CONTRA_MIN and s >= settings.CONFLICT_SUPPORT_MIN


def action_for(band: str, conflicted: bool) -> str:
    if conflicted:
        return "keep"
    if band in ("VERIFIED", "PARTIAL"):
        return "keep"
    if band == "WEAK":
        return "hedge"
    return "drop"


def score_claim(claim: Claim) -> Claim:
    """Populate every CEVS field on a claim from its evidence verdicts. Mutates in place."""
    theta = settings.ENTAIL_THRESHOLD
    v = claim.verdicts

    claim.support = support_score(v)
    claim.agreement = agreement_score(v, theta)
    claim.authority = authority_score(v, theta)
    claim.contradiction = contradiction_score(v)
    claim.confidence = claim_confidence(
        claim.support, claim.agreement, claim.authority, claim.contradiction
    )
    claim.band = verdict_band(claim.confidence)
    claim.conflicted = is_conflicted(claim.support, claim.contradiction)
    claim.action = action_for(claim.band, claim.conflicted)
    return claim


# ------------------------------------------------------------------ aggregate


def aggregate_confidence(claims: List[Claim], distinct_domains: int) -> Tuple[float, List[str]]:
    """C_final — centrality-weighted mean, then conflict and single-source penalties."""
    kept = [c for c in claims if c.action != "drop"]
    pool = kept or claims
    if not pool:
        return 0.0, ["no_claims_extracted"]

    total_w = sum(c.weight for c in pool)
    base = sum(c.weight * c.confidence for c in pool) / max(total_w, 1e-9)

    flags: List[str] = []
    conf = base

    if any(c.conflicted for c in pool):
        conf *= 1.0 - settings.PENALTY_CONFLICT
        flags.append("conflicting_sources")

    if distinct_domains < 2:
        conf *= 1.0 - settings.PENALTY_SINGLE_SOURCE
        flags.append("single_source_answer")

    dropped = [c for c in claims if c.action == "drop"]
    if dropped:
        flags.append(f"dropped_{len(dropped)}_unsupported_claims")
    hedged = [c for c in claims if c.action == "hedge"]
    if hedged:
        flags.append(f"hedged_{len(hedged)}_weak_claims")

    uncited = [c for c in pool if not c.verdicts]
    if uncited:
        flags.append(f"{len(uncited)}_claims_had_no_retrievable_evidence")

    return _clamp01(conf), flags


def failing_claims(claims: List[Claim]) -> List[Claim]:
    """Claims worth spending another research round on.

    Conflicted claims are excluded: more searching will not resolve a genuine
    disagreement between sources, and surfacing it is the correct behaviour.
    """
    return [
        c
        for c in claims
        if c.confidence < settings.ENTAIL_THRESHOLD and not c.conflicted
    ]


def signal_breakdown(claim: Claim) -> Dict[str, float]:
    """Per-signal contribution to the final score, for the audit / explain view."""
    return {
        "support_contrib": round(settings.W_SUPPORT * claim.support, 4),
        "agreement_contrib": round(settings.W_AGREEMENT * claim.agreement, 4),
        "authority_contrib": round(settings.W_AUTHORITY * claim.authority, 4),
        "contradiction_penalty": round(-settings.W_CONTRADICTION * claim.contradiction, 4),
        "total": round(claim.confidence, 4),
    }
