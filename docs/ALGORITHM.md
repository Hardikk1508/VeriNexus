# VeriNexus Core Algorithm — CEVS

**Claim–Evidence Verification & Scoring**

This is the part of VeriNexus that is not "just RAG". A normal RAG system retrieves,
generates, and stops. CEVS adds a closed verification loop: the generated answer is
decomposed back into atomic claims, each claim is re-grounded against the evidence pool
that was actually retrieved, and the answer is only released once every claim carries a
computed support score — or is rewritten, hedged, or dropped.

---

## 0. Notation

| Symbol | Meaning |
|---|---|
| `Q` | user research question |
| `q_i` | sub-question produced by the Planner |
| `E` | evidence pool — set of retrieved chunks for this session |
| `e` | one evidence chunk `{text, source_id, domain, published, offset}` |
| `D` | set of distinct source domains in `E` |
| `c_j` | atomic claim extracted from the draft answer |
| `E_j ⊂ E` | evidence retrieved specifically for claim `c_j` |
| `λ_j` | centrality weight of claim `c_j` (core = 1.0, supporting = 0.5) |
| `θ_e` | entailment acceptance threshold (default 0.60) |
| `τ_r` | human-review threshold (default 0.55) |

---

## 1. Pipeline

```
Q
│
├─ [1] PLAN          Q  ──►  {q_1..q_n}, search_queries, recency_flag
│
├─ [2] RESEARCH      search + fetch + chunk + embed  ──►  E
│
├─ [3] DRAFT         Q + top-k(E)  ──►  A_draft with inline [E#] markers
│
├─ [4] DECOMPOSE     A_draft  ──►  {c_1..c_m} with λ_j
│
├─ [5] VERIFY        ∀ c_j:  E_j = hybrid_retrieve(c_j, E, k=5)
│                            NLI(e → c_j) ∀ e ∈ E_j
│                            conf(c_j) = CEVS score
│
├─ [6] GATE          any conf(c_j) < θ_e and round < R ?
│         ├─ yes ──► [6a] TARGETED RESEARCH on failing claims, E ← E ∪ E'  ──► back to [3]
│         └─ no  ──► [7]
│
├─ [7] FINALISE      rewrite/hedge/drop weak claims, attach citations, aggregate confidence
│
└─ [8] ROUTE         C_answer < τ_r  ──►  flag for human review
```

Loop bound `R = 2`. This guarantees termination: worst case 3 draft passes.

---

## 2. Hybrid retrieval (steps 2, 5, 6a)

Dense-only retrieval misses exact entities (names, numbers, statute IDs) which are
exactly the tokens a verification step cares about. So retrieval is fused:

1. **Dense**: cosine similarity over `all-MiniLM-L6-v2` embeddings (384-d), via Chroma.
2. **Sparse**: BM25 over the same chunk set.
3. **Fusion**: Reciprocal Rank Fusion.

```
RRF(d) = Σ_over_rankers  1 / (k + rank_r(d))        k = 60
```

RRF is used instead of score normalisation because BM25 and cosine live on
incomparable scales; RRF only needs ranks.

---

## 3. Natural Language Inference

For each `(e, c_j)` pair the verifier produces a three-way distribution:

```
NLI(e, c_j)  ──►  (p_entail, p_contradict, p_neutral),  Σ = 1
```

Two interchangeable backends (config flag `NLI_BACKEND`):

- **`llm`** (default) — Groq `llama-3.3-70b-versatile` as a constrained judge returning
  JSON. Zero download, ~200 ms/claim batch.
- **`local`** — `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` cross-encoder. Slower on
  CPU but gives calibrated probabilities and lets the report claim a non-LLM
  verification signal, which strengthens the evaluation chapter.

Premise = evidence chunk. Hypothesis = claim. **Never the reverse** — entailment is
directional, and getting it backwards is the single most common bug in claim-verification
systems.

---

## 4. The four signals

For claim `c_j` with evidence set `E_j`:

**Support** — the strongest single piece of evidence.
```
S(c_j) = max_{e ∈ E_j}  p_entail(e, c_j)
```

**Agreement** — independent corroboration. Two chunks from the same domain are not two
witnesses, so agreement is measured over *distinct domains*.
```
D_j⁺ = { domain(e) : e ∈ E_j,  p_entail(e, c_j) > θ_e }
D_j  = { domain(e) : e ∈ E_j }

A(c_j) = |D_j⁺| / max(|D_j|, 1)
```

**Authority** — mean source quality of the supporting evidence only.
```
Q(c_j) = mean_{e : p_entail > θ_e}  auth(domain(e))
```
`auth` is a tiered heuristic in `core/scoring.py`: peer-reviewed / .gov / .edu = 0.95,
established reference & major press = 0.75, user-uploaded documents = 0.80 (the user
vouched for them), unknown blogs/forums = 0.35.

**Contradiction** — the strongest opposing evidence found anywhere in the pool.
```
X(c_j) = max_{e ∈ E_j}  p_contradict(e, c_j)
```

## 5. Claim confidence

```
conf(c_j) = clamp₀¹(  w_s·S(c_j) + w_a·A(c_j) + w_q·Q(c_j) − w_x·X(c_j)  )

w_s = 0.45   w_a = 0.25   w_q = 0.15   w_x = 0.35
```

Design notes worth defending in a viva:

- `w_s + w_a + w_q = 0.85`, not 1.0. A claim supported by one perfect source with no
  corroboration should *not* reach certainty. The ceiling is earned, not given.
- `w_x = 0.35` is deliberately larger than `w_a`. One credible contradiction should
  outweigh a second agreeing source. Disagreement is more informative than agreement.
- Contradiction is subtracted rather than gated, so a claim with strong support *and*
  strong contradiction lands in the middle band and gets surfaced as **conflicted**
  rather than silently dropped.

**Verdict bands**

| conf | Verdict | UI |
|---|---|---|
| ≥ 0.75 | `VERIFIED` | green chip |
| 0.55 – 0.75 | `PARTIAL` | amber chip |
| 0.30 – 0.55 | `WEAK` | amber chip, hedged wording |
| < 0.30 | `UNSUPPORTED` | red chip, dropped or explicitly marked |

Separately, `X(c_j) > 0.5` **and** `S(c_j) > 0.5` ⟹ `CONFLICTED`, regardless of band.

## 6. Answer-level confidence

Centrality-weighted mean — a shaky throwaway aside should not sink an otherwise solid
answer, and a shaky *central* claim should:

```
C_answer = Σ_j λ_j · conf(c_j)  /  Σ_j λ_j
```

Then two penalties applied after aggregation:

```
C_final = C_answer × (1 − 0.15·𝟙[any CONFLICTED]) × (1 − 0.20·𝟙[|D| < 2])
```

The second penalty is the single-source penalty: an answer resting entirely on one
domain is capped, no matter how well that domain entails it.

---

## 7. Repair policy (step 6a)

For each claim with `conf(c_j) < θ_e`:

1. **Re-research** — generate 1–2 targeted queries from the claim text itself (not the
   original question) and add results to `E`. This is where the loop earns its keep:
   the draft has told us precisely what we failed to find.
2. If still failing after `R` rounds, apply one of:
   - `WEAK` → rewrite with an explicit hedge and keep the citation.
   - `UNSUPPORTED` → drop the sentence, log the drop in the audit trail.
   - `CONFLICTED` → keep, but render both sides with their respective sources.

Every drop and every hedge is recorded. The audit trail is what makes the system
*auditable* rather than merely *cautious*.

---

## 8. Complexity

Let `n` = sub-questions, `m` = claims, `k` = evidence per claim, `R` = repair rounds.

| Stage | LLM calls | Cost driver |
|---|---|---|
| Plan | 1 | — |
| Research | 0 | `n` web calls |
| Draft | 1 per round | context = top-k(E) |
| Decompose | 1 per round | length of draft |
| Verify | `m` per round (batched over `k`) | dominant term |
| Finalise | 1 | — |

Total ≈ `1 + (R+1)·(2 + m) + 1`. With `m ≈ 8`, `R = 1`: ~22 calls. On Groq at
~500 tok/s this is a 20–40 s end-to-end response, which is why the API streams
per-stage events instead of blocking.

Embedding and BM25 are `O(|E|)` and run locally — negligible.

---

## 9. Evaluation design

Baselines to compare against on the same 40-question set:

| System | Description |
|---|---|
| B0 | Bare LLM, no retrieval |
| B1 | Vanilla RAG (retrieve → generate) |
| B2 | RAG + "cite your sources" prompt |
| **V** | **VeriNexus (full CEVS loop)** |

Metrics:

- **Unsupported-claim rate** — fraction of atomic claims in the final answer with no
  entailing evidence, judged by a *held-out* human/LLM annotator, not by VeriNexus's own
  verifier. This is the headline number.
- **Citation precision** — cited chunk actually supports the sentence it is attached to.
- **Citation recall** — verifiable sentences that carry a citation.
- **Conflict detection rate** — on the 10 deliberately-planted contradictory-source
  questions, does the system flag the conflict?
- **Confidence calibration** — bin claims by predicted `conf`, plot against observed
  correctness; report Expected Calibration Error. A trust system whose confidence score
  is uncalibrated is worse than one with no score at all, so this belongs in the report.
- **Latency** and **cost per query**, to show the honest trade-off.

Expected result to aim for: V roughly halves unsupported-claim rate vs B2 at ~3× latency.
Report the latency cost openly — a defended trade-off reads better than a hidden one.
