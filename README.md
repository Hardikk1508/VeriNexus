# VeriNexus

A trustworthy agentic AI system for evidence-based research and verified information synthesis.

Instead of answering immediately, VeriNexus plans, retrieves, drafts, decomposes the draft
into atomic claims, verifies each claim against the evidence it retrieved, and only then
writes the answer — attaching a confidence score and flagging anything it could not support.

The scoring core is specified in [`docs/ALGORITHM.md`](docs/ALGORITHM.md).

---

## Project layout

```
verinexus/
├── docs/
│   └── ALGORITHM.md          # CEVS derivation — the formal spec
├── backend/
│   ├── app/
│   │   ├── config.py         # every tunable constant, one source of truth
│   │   ├── schemas.py        # Chunk, Source, Claim, EvidenceVerdict, API models
│   │   ├── graph.py          # LangGraph state machine wiring the agents
│   │   ├── audit.py          # per-step audit trail (MongoDB, optional)
│   │   ├── main.py           # FastAPI routes incl. SSE streaming
│   │   ├── core/
│   │   │   ├── ingest.py     # PDF parsing, chunking, Tavily web search
│   │   │   ├── llm.py        # Groq client, retry/backoff, safe JSON parsing
│   │   │   ├── retrieval.py  # dense + BM25 + Reciprocal Rank Fusion
│   │   │   └── scoring.py    # CEVS — pure, I/O-free, fully unit-tested
│   │   └── agents/
│   │       ├── planner.py    # question → sub-questions
│   │       ├── researcher.py # sub-questions → evidence pool
│   │       ├── verifier.py   # NLI entailment per (claim, evidence) pair
│   │       └── responder.py  # draft, decompose, finalise
│   ├── tests/test_scoring.py # 35 tests, no keys or network needed
│   ├── scripts/smoke_test.py # end-to-end check against a running server
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── App.jsx           # query box, live pipeline, claim chips
    │   ├── api.js            # REST + SSE client
    │   ├── main.jsx
    │   └── index.css
    ├── index.html
    ├── vite.config.js        # proxies /api → localhost:8000 in dev
    └── package.json
```

---

## Setup in VS Code

Open the `verinexus` folder as the workspace root, then use two terminals.

### 1. Backend

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Open `.env` and set at minimum:

```
GROQ_API_KEY=gsk_...
```

`TAVILY_API_KEY` unlocks web evidence; without it, research runs on uploaded
documents only. `MONGODB_URI` enables the persistent audit trail; without it the
server still runs and `/api/health` reports what is missing.

Run it:

```bash
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend, so SSE and CORS
both work without extra configuration.

### Recommended VS Code extensions

- **Python** + **Pylance** — set the interpreter to `backend/.venv`
- **Ruff** — linting
- **ESLint** — frontend
- **Thunder Client** or **REST Client** — poking endpoints without leaving the editor

---

## Verify your setup

Before wiring anything new, confirm the core works:

```bash
cd backend
pytest                       # 35 tests, runs in well under a second
```

These cover the scoring maths only — no API keys, no models, no network. If they
pass, `config.py` and `scoring.py` agree with each other.

With the server running:

```bash
python scripts/smoke_test.py
```

This checks `/api/health`, then streams a full research run and prints each
pipeline stage as it completes.

---

## API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | status, model, NLI backend, missing config |
| `POST` | `/api/upload` | multipart `session_id` + `file` — ingest a PDF |
| `POST` | `/api/research` | blocking run, returns the full `ResearchResponse` |
| `GET` | `/api/research/stream` | same run as Server-Sent Events, stage by stage |
| `GET` | `/api/runs` | recent runs from the audit trail |
| `GET` | `/api/runs/{session_id}` | one run in full, replayable |
| `POST` | `/api/runs/{session_id}/review` | record a human review decision |

---

## How a claim is scored

Four signals per claim, over its retrieved evidence set:

| Signal | Meaning |
|---|---|
| **Support** `S` | strongest single entailment probability |
| **Agreement** `A` | fraction of *distinct domains* that entail it |
| **Authority** `Q` | mean source quality of the supporting evidence only |
| **Contradiction** `X` | strongest opposing evidence found anywhere |

```
conf(c) = clamp₀¹( 0.45·S + 0.25·A + 0.15·Q − 0.35·X )
```

The positive weights sum to **0.85, not 1.0** — one perfect uncorroborated source
must not reach certainty. `w_x` (0.35) exceeds `w_a` (0.25) because a credible
contradiction should outweigh a second agreeing source.

| Confidence | Band | Action |
|---|---|---|
| ≥ 0.75 | `VERIFIED` | keep |
| 0.55 – 0.75 | `PARTIAL` | keep |
| 0.30 – 0.55 | `WEAK` | hedge the wording |
| < 0.30 | `UNSUPPORTED` | drop from the answer |

Separately, `X > 0.5` **and** `S > 0.5` marks the claim `CONFLICTED`. Conflicted
claims are always kept and surfaced — never silently resolved.

Answers below `REVIEW_THRESHOLD` (0.55) are flagged for human review.

---

## Notes

- Chunking uses word windows rather than sentence splitting: verification needs
  chunks long enough to carry a full premise, and sentence splitters fragment
  numeric and tabular content badly.
- Retrieval fuses dense and BM25 rankings with RRF (`k = 60`) because cosine and
  BM25 scores live on incomparable scales; RRF only needs ranks.
- NLI is directional. `premise = evidence`, `hypothesis = claim`. Getting this
  backwards is the most common bug in claim-verification systems.
- The repair loop is bounded at `MAX_REPAIR_ROUNDS = 2`, guaranteeing termination
  at worst after three draft passes.
