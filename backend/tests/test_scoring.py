"""Unit tests for CEVS — the pure scoring core.

These need no API keys, no models and no network: scoring.py is deliberately
I/O-free. Run with:  pytest backend/tests -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.core import scoring
from app.schemas import Claim, EvidenceVerdict


def verdict(domain, entail, contra, authority=0.8, chunk_id="c1"):
    return EvidenceVerdict(
        chunk_id=chunk_id,
        source_id=f"s-{domain}",
        domain=domain,
        snippet="...",
        authority=authority,
        p_entail=entail,
        p_contradict=contra,
        p_neutral=max(0.0, 1.0 - entail - contra),
    )


# ---------------------------------------------------------------- authority

@pytest.mark.parametrize(
    "domain,expected",
    [
        ("arxiv.org", 0.95),
        ("pubmed.ncbi.nlm.nih.gov", 0.95),
        ("niti.gov.in", 0.88),
        ("upes.ac.in", 0.88),
        ("reuters.com", 0.75),
        ("wikipedia.org", 0.65),
        ("github.com", 0.55),
        ("medium.com", 0.35),
        ("some-random-site.xyz", 0.50),
        ("", 0.35),
    ],
)
def test_authority_tiers(domain, expected):
    assert scoring.source_authority(domain) == expected


def test_uploads_are_trusted_but_not_peer_reviewed():
    assert scoring.source_authority("localfile", kind="upload") == 0.80


def test_domain_of_strips_www():
    assert scoring.domain_of("https://www.nature.com/articles/x") == "nature.com"
    assert scoring.domain_of("not a url") == "unknown"


# ---------------------------------------------------------------- signals

def test_support_is_the_strongest_single_witness():
    v = [verdict("a.com", 0.30, 0.0), verdict("b.com", 0.91, 0.0)]
    assert scoring.support_score(v) == pytest.approx(0.91)


def test_agreement_counts_domains_not_chunks():
    """Three chunks from one domain must count as one witness."""
    same = [verdict("a.com", 0.9, 0.0, chunk_id=f"c{i}") for i in range(3)]
    assert scoring.agreement_score(same, 0.6) == pytest.approx(1.0)

    mixed = same + [verdict("b.com", 0.1, 0.0), verdict("c.com", 0.1, 0.0)]
    # 1 of 3 distinct domains entails
    assert scoring.agreement_score(mixed, 0.6) == pytest.approx(1 / 3)


def test_authority_uses_supporting_evidence_only():
    v = [verdict("arxiv.org", 0.9, 0.0, authority=0.95),
         verdict("medium.com", 0.1, 0.0, authority=0.35)]
    assert scoring.authority_score(v, 0.6) == pytest.approx(0.95)


def test_empty_evidence_scores_zero():
    assert scoring.support_score([]) == 0.0
    assert scoring.agreement_score([], 0.6) == 0.0
    assert scoring.authority_score([], 0.6) == 0.0
    assert scoring.contradiction_score([]) == 0.0


# ---------------------------------------------------------------- confidence

def test_one_perfect_source_cannot_reach_certainty():
    """w_s + w_a + w_q = 0.85 by design, so a lone witness caps below 1.0."""
    conf = scoring.claim_confidence(s=1.0, a=1.0, q=1.0, x=0.0)
    assert conf == pytest.approx(0.85)
    assert conf < 1.0


def test_contradiction_outweighs_a_second_agreeing_source():
    assert settings.W_CONTRADICTION > settings.W_AGREEMENT


def test_confidence_is_clamped():
    assert scoring.claim_confidence(1.0, 1.0, 1.0, 1.0) >= 0.0
    assert scoring.claim_confidence(0.0, 0.0, 0.0, 1.0) == 0.0


@pytest.mark.parametrize(
    "conf,band",
    [(0.95, "VERIFIED"), (0.75, "VERIFIED"), (0.60, "PARTIAL"),
     (0.55, "PARTIAL"), (0.40, "WEAK"), (0.30, "WEAK"), (0.10, "UNSUPPORTED")],
)
def test_verdict_bands(conf, band):
    assert scoring.verdict_band(conf) == band


def test_conflict_needs_strength_on_both_sides():
    assert scoring.is_conflicted(s=0.8, x=0.7) is True
    assert scoring.is_conflicted(s=0.8, x=0.2) is False   # no real opposition
    assert scoring.is_conflicted(s=0.2, x=0.8) is False   # nothing to oppose


def test_conflicted_claims_are_kept_not_dropped():
    """Surfacing disagreement is the product; silently dropping it is the bug."""
    assert scoring.action_for("UNSUPPORTED", conflicted=True) == "keep"
    assert scoring.action_for("UNSUPPORTED", conflicted=False) == "drop"
    assert scoring.action_for("WEAK", conflicted=False) == "hedge"


# ---------------------------------------------------------------- end to end

def test_score_claim_populates_every_field():
    c = Claim(claim_id="c1", text="Transformers parallelise training.")
    c.verdicts = [
        verdict("arxiv.org", 0.92, 0.02, authority=0.95),
        verdict("aclanthology.org", 0.81, 0.01, authority=0.95),
    ]
    scoring.score_claim(c)
    assert c.support == pytest.approx(0.92)
    assert c.agreement == pytest.approx(1.0)
    assert c.band == "VERIFIED"
    assert c.conflicted is False
    assert c.action == "keep"


def test_unsupported_claim_is_dropped():
    c = Claim(claim_id="c2", text="Training cost fell by exactly 40%.")
    c.verdicts = [verdict("blog.example.com", 0.05, 0.10, authority=0.35)]
    scoring.score_claim(c)
    assert c.band == "UNSUPPORTED"
    assert c.action == "drop"


def test_aggregate_penalises_conflict_and_single_source():
    good = Claim(claim_id="a", text="x")
    good.verdicts = [verdict("arxiv.org", 0.95, 0.0, authority=0.95),
                     verdict("nature.com", 0.90, 0.0, authority=0.95)]
    scoring.score_claim(good)

    clean, flags = scoring.aggregate_confidence([good], distinct_domains=2)
    assert flags == []

    penalised, flags = scoring.aggregate_confidence([good], distinct_domains=1)
    assert "single_source_answer" in flags
    assert penalised == pytest.approx(clean * (1 - settings.PENALTY_SINGLE_SOURCE))


def test_aggregate_weights_core_claims_above_supporting():
    core = Claim(claim_id="a", text="x", centrality="core")
    core.verdicts = [verdict("arxiv.org", 0.95, 0.0)]
    scoring.score_claim(core)

    sup = Claim(claim_id="b", text="y", centrality="supporting")
    sup.verdicts = [verdict("arxiv.org", 0.95, 0.0)]
    scoring.score_claim(sup)

    assert core.weight == 1.0 and sup.weight == 0.5


def test_aggregate_handles_no_claims():
    conf, flags = scoring.aggregate_confidence([], distinct_domains=0)
    assert conf == 0.0
    assert "no_claims_extracted" in flags


def test_failing_claims_excludes_conflicted():
    """Re-searching will not resolve a genuine source disagreement."""
    weak = Claim(claim_id="a", text="x")
    weak.verdicts = [verdict("blog.com", 0.1, 0.0, authority=0.35)]
    scoring.score_claim(weak)

    clash = Claim(claim_id="b", text="y")
    clash.verdicts = [verdict("a.com", 0.8, 0.0), verdict("b.com", 0.0, 0.8)]
    scoring.score_claim(clash)
    assert clash.conflicted is True

    failing = scoring.failing_claims([weak, clash])
    ids = {c.claim_id for c in failing}
    assert "a" in ids and "b" not in ids


def test_signal_breakdown_sums_to_confidence():
    c = Claim(claim_id="c1", text="x")
    c.verdicts = [verdict("arxiv.org", 0.9, 0.1, authority=0.95)]
    scoring.score_claim(c)
    b = scoring.signal_breakdown(c)
    total = (b["support_contrib"] + b["agreement_contrib"]
             + b["authority_contrib"] + b["contradiction_penalty"])
    assert total == pytest.approx(b["total"], abs=1e-3)
