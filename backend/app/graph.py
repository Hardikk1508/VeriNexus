"""LangGraph workflow.

    plan -> research -> draft -> decompose -> verify -> [repair -> draft] -> finalise

The repair edge is the whole point of the system: the draft tells us precisely which
claims failed, so the second research round is aimed at those claims rather than at the
original question. Bounded by MAX_REPAIR_ROUNDS so termination is guaranteed.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agents import planner, researcher, responder, verifier
from app.config import settings
from app.core.retrieval import EvidenceStore
from app.core.scoring import aggregate_confidence, failing_claims, verdict_band
from app.schemas import Claim

log = logging.getLogger("verinexus.graph")

_STORES: Dict[str, EvidenceStore] = {}


def get_store(session_id: str) -> EvidenceStore:
    if session_id not in _STORES:
        _STORES[session_id] = EvidenceStore(session_id)
    return _STORES[session_id]


def drop_store(session_id: str) -> None:
    store = _STORES.pop(session_id, None)
    if store is not None:
        store.close()


class GraphState(TypedDict, total=False):
    session_id: str
    query: str
    uploads: List[Dict[str, str]]
    max_rounds: int

    plan: Dict[str, Any]
    sources: List[Dict[str, Any]]
    draft: str
    marker_map: Dict[str, Any]
    claims: List[Claim]
    answer: str
    confidence: float
    band: str
    flags: List[str]
    needs_human_review: bool
    repair_round: int
    audit: List[Dict[str, Any]]
    stats: Dict[str, Any]


def _audit(state: GraphState, step: str, label: str, t0: float, detail: Dict[str, Any]):
    state.setdefault("audit", []).append(
        {
            "step": step,
            "label": label,
            "started_at": t0,
            "duration_ms": int((time.time() - t0) * 1000),
            "detail": detail,
        }
    )


# ------------------------------------------------------------------ nodes


def node_plan(state: GraphState) -> GraphState:
    t0 = time.time()
    p = planner.plan(state["query"])
    state["plan"] = p
    state.setdefault("repair_round", 0)
    state.setdefault("sources", [])
    _audit(
        state, "plan", "Planner Agent", t0,
        {
            "intent": p["intent"],
            "sub_questions": [s["question"] for s in p["sub_questions"]],
            "needs_recency": p["needs_recency"],
            "contested": p["contested"],
            "degraded": p.get("degraded", False),
        },
    )
    return state


def node_research(state: GraphState) -> GraphState:
    t0 = time.time()
    store = get_store(state["session_id"])

    up_sources: List = []
    up_chunks = 0
    if state.get("uploads"):
        up_sources, up_chunks = researcher.load_uploads(store, state["uploads"])

    queries = planner.all_search_queries(state["plan"])
    web_sources, web_chunks = researcher.research(
        store, queries, deep=state["plan"].get("contested", False)
    )

    state["sources"] = researcher.merge_sources(
        state.get("sources", []), list(up_sources) + list(web_sources)
    )
    _audit(
        state, "research", "Research Agent", t0,
        {
            "queries": queries,
            "new_sources": len(up_sources) + len(web_sources),
            "chunks_added": up_chunks + web_chunks,
            "pool_size": store.size,
            "distinct_domains": len(store.domains),
        },
    )
    return state


def node_draft(state: GraphState) -> GraphState:
    t0 = time.time()
    store = get_store(state["session_id"])
    context = store.search_diverse(
        state["query"], k=settings.DRAFT_CONTEXT_CHUNKS, per_domain=3
    )
    text, mapping = responder.draft(state["query"], state["plan"], context)
    state["draft"] = text
    state["marker_map"] = mapping
    _audit(
        state, "draft", "Response Agent (draft)", t0,
        {
            "context_chunks": len(context),
            "context_domains": sorted({c.domain for c in context}),
            "words": len(text.split()),
            "round": state.get("repair_round", 0),
        },
    )
    return state


def node_decompose(state: GraphState) -> GraphState:
    t0 = time.time()
    claims = responder.extract_claims(state["query"], state["draft"])
    responder.attach_citations(claims, state.get("marker_map", {}))
    state["claims"] = claims
    _audit(
        state, "decompose", "Claim Extraction", t0,
        {
            "claim_count": len(claims),
            "core": sum(1 for c in claims if c.centrality == "core"),
            "supporting": sum(1 for c in claims if c.centrality == "supporting"),
            "claims": [c.text for c in claims],
        },
    )
    return state


def node_verify(state: GraphState) -> GraphState:
    t0 = time.time()
    store = get_store(state["session_id"])
    claims = verifier.verify_all(state.get("claims", []), store)
    state["claims"] = claims
    _audit(
        state, "verify", "Verification Agent", t0,
        {
            "backend": settings.NLI_BACKEND,
            "checked": len(claims),
            "verified": sum(1 for c in claims if c.band == "VERIFIED"),
            "partial": sum(1 for c in claims if c.band == "PARTIAL"),
            "weak": sum(1 for c in claims if c.band == "WEAK"),
            "unsupported": sum(1 for c in claims if c.band == "UNSUPPORTED"),
            "conflicted": sum(1 for c in claims if c.conflicted),
            "per_claim": [
                {
                    "id": c.claim_id,
                    "band": c.band,
                    "confidence": round(c.confidence, 3),
                    "S": round(c.support, 3),
                    "A": round(c.agreement, 3),
                    "Q": round(c.authority, 3),
                    "X": round(c.contradiction, 3),
                }
                for c in claims
            ],
        },
    )
    return state


def node_repair(state: GraphState) -> GraphState:
    t0 = time.time()
    store = get_store(state["session_id"])
    weak = failing_claims(state.get("claims", []))
    queries = responder.repair_queries(weak)
    new_sources, added = researcher.research(store, queries, deep=True)
    state["sources"] = researcher.merge_sources(state.get("sources", []), new_sources)
    state["repair_round"] = state.get("repair_round", 0) + 1
    _audit(
        state, "repair", "Targeted Re-research", t0,
        {
            "round": state["repair_round"],
            "targeted_claims": [c.text for c in weak],
            "queries": queries,
            "chunks_added": added,
            "pool_size": store.size,
        },
    )
    return state


def node_finalise(state: GraphState) -> GraphState:
    t0 = time.time()
    store = get_store(state["session_id"])
    claims = state.get("claims", [])

    answer = responder.finalise(state["query"], state.get("draft", ""), claims)
    conf, flags = aggregate_confidence(claims, len(store.domains))

    state["answer"] = answer
    state["confidence"] = conf
    state["band"] = verdict_band(conf)
    state["flags"] = flags
    state["needs_human_review"] = conf < settings.REVIEW_THRESHOLD or any(
        c.conflicted for c in claims
    )
    state["stats"] = {
        "evidence_chunks": store.size,
        "distinct_domains": len(store.domains),
        "sources": len(state.get("sources", [])),
        "claims": len(claims),
        "repair_rounds": state.get("repair_round", 0),
        "kept": sum(1 for c in claims if c.action == "keep"),
        "hedged": sum(1 for c in claims if c.action == "hedge"),
        "dropped": sum(1 for c in claims if c.action == "drop"),
    }
    _audit(
        state, "finalise", "Final Synthesis", t0,
        {
            "confidence": round(conf, 3),
            "band": state["band"],
            "flags": flags,
            "needs_human_review": state["needs_human_review"],
        },
    )
    return state


# ------------------------------------------------------------------ routing


def route_after_verify(state: GraphState) -> str:
    weak = failing_claims(state.get("claims", []))
    limit = state.get("max_rounds", settings.MAX_REPAIR_ROUNDS)
    if weak and state.get("repair_round", 0) < limit:
        return "repair"
    return "finalise"


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("plan", node_plan)
    g.add_node("research", node_research)
    g.add_node("draft", node_draft)
    g.add_node("decompose", node_decompose)
    g.add_node("verify", node_verify)
    g.add_node("repair", node_repair)
    g.add_node("finalise", node_finalise)

    g.set_entry_point("plan")
    g.add_edge("plan", "research")
    g.add_edge("research", "draft")
    g.add_edge("draft", "decompose")
    g.add_edge("decompose", "verify")
    g.add_conditional_edges(
        "verify", route_after_verify, {"repair": "repair", "finalise": "finalise"}
    )
    g.add_edge("repair", "draft")
    g.add_edge("finalise", END)
    return g.compile()


WORKFLOW = build_graph()


def initial_state(
    query: str,
    session_id: Optional[str] = None,
    uploads: Optional[List[Dict[str, str]]] = None,
    max_rounds: Optional[int] = None,
) -> GraphState:
    return {
        "session_id": session_id or uuid.uuid4().hex,
        "query": query.strip(),
        "uploads": uploads or [],
        "max_rounds": settings.MAX_REPAIR_ROUNDS if max_rounds is None else max_rounds,
        "repair_round": 0,
        "sources": [],
        "audit": [],
    }


def serialise(state: GraphState) -> Dict[str, Any]:
    return {
        "session_id": state.get("session_id", ""),
        "query": state.get("query", ""),
        "answer": state.get("answer", ""),
        "confidence": round(state.get("confidence", 0.0), 3),
        "band": state.get("band", "UNSUPPORTED"),
        "needs_human_review": state.get("needs_human_review", True),
        "claims": [c.to_dict() for c in state.get("claims", [])],
        "sources": state.get("sources", []),
        "flags": state.get("flags", []),
        "audit": state.get("audit", []),
        "stats": state.get("stats", {}),
    }
