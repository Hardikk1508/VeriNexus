"""VeriNexus API.

Endpoints
    GET  /api/health
    POST /api/research            blocking, returns the full verified result
    GET  /api/research/stream     SSE, emits one event per pipeline stage
    POST /api/upload              attach a PDF to a session
    GET  /api/runs                recent runs (needs MongoDB)
    GET  /api/runs/{session_id}   full audit record
    POST /api/runs/{sid}/review   record a human review decision
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import audit, graph
from app.config import settings
from app.core import auth as fb_auth
from app.core.auth import require_user
from app.schemas import ResearchRequest

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("verinexus.api")

app = FastAPI(title="VeriNexus", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "verinexus_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
_SESSION_UPLOADS: Dict[str, List[Dict[str, str]]] = {}

STAGE_LABELS = {
    "plan": "Breaking the question into research tasks",
    "research": "Gathering sources",
    "draft": "Drafting a grounded answer",
    "decompose": "Splitting the answer into checkable claims",
    "verify": "Checking every claim against the evidence",
    "repair": "Re-researching the claims that failed",
    "finalise": "Assembling the verified answer",
}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model": settings.GROQ_MODEL,
        "nli_backend": settings.NLI_BACKEND,
        "audit_persistence": audit.db() is not None,
        "auth_enabled": fb_auth.enabled(),
        "missing_config": settings.validate(),
    }


# ------------------------------------------------------------------ upload


@app.post("/api/upload")
async def upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF uploads are supported in this version.")
    dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}.pdf")
    with open(dest, "wb") as fh:
        fh.write(await file.read())
    _SESSION_UPLOADS.setdefault(session_id, []).append(
        {"path": dest, "name": file.filename}
    )
    return {
        "session_id": session_id,
        "name": file.filename,
        "attached": len(_SESSION_UPLOADS[session_id]),
    }


# ------------------------------------------------------------------ research


def _run_blocking(req: ResearchRequest) -> Dict[str, Any]:
    sid = req.session_id or uuid.uuid4().hex
    state = graph.initial_state(
        req.query,
        session_id=sid,
        uploads=_SESSION_UPLOADS.get(sid, []),
        max_rounds=req.max_repair_rounds,
    )
    final = graph.WORKFLOW.invoke(state, config={"recursion_limit": 40})
    payload = graph.serialise(final)
    audit.save_run(payload)
    graph.drop_store(sid)
    _SESSION_UPLOADS.pop(sid, None)
    return payload


@app.post("/api/research")
async def research(req: ResearchRequest, user: dict = Depends(require_user)):
    if settings.validate() and not settings.GROQ_API_KEY:
        raise HTTPException(500, "GROQ_API_KEY is not configured on the server.")
    try:
        return await asyncio.to_thread(_run_blocking, req)
    except Exception as exc:  # noqa: BLE001
        log.exception("research failed")
        raise HTTPException(500, f"Pipeline error: {exc}") from exc


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _stage_payload(node: str, update: Dict[str, Any]) -> Dict[str, Any]:
    """Trim node output down to what the UI needs for the live pipeline view."""
    trail = update.get("audit") or []
    detail = trail[-1].get("detail", {}) if trail else {}
    duration = trail[-1].get("duration_ms", 0) if trail else 0
    out = {
        "stage": node,
        "label": STAGE_LABELS.get(node, node),
        "duration_ms": duration,
        "detail": detail,
    }
    if node == "verify":
        out["claims"] = [c.to_dict() for c in update.get("claims", [])]
    if node == "research" or node == "repair":
        out["sources"] = update.get("sources", [])
    return out


@app.get("/api/research/stream")
async def research_stream(
    q: str, session_id: str = "", max_repair_rounds: int = -1, token: str = ""
):
    if len(q.strip()) < 8:
        raise HTTPException(400, "Query is too short.")
    # EventSource (browser SSE client) cannot send custom headers, so the ID
    # token travels as a query param here instead of Authorization.
    fb_auth.verify_token(token)

    sid = session_id or uuid.uuid4().hex
    rounds = None if max_repair_rounds < 0 else max_repair_rounds

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker():
            state = graph.initial_state(
                q, session_id=sid, uploads=_SESSION_UPLOADS.get(sid, []), max_rounds=rounds
            )
            final_state: Dict[str, Any] = {}
            try:
                for chunk in graph.WORKFLOW.stream(
                    state, config={"recursion_limit": 40}, stream_mode="updates"
                ):
                    for node, update in chunk.items():
                        final_state.update(update)
                        loop.call_soon_threadsafe(
                            queue.put_nowait, ("stage", _stage_payload(node, final_state))
                        )
                payload = graph.serialise(final_state)
                audit.save_run(payload)
                loop.call_soon_threadsafe(queue.put_nowait, ("done", payload))
            except Exception as exc:  # noqa: BLE001
                log.exception("stream pipeline failed")
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", {"message": str(exc)})
                )
            finally:
                graph.drop_store(sid)
                _SESSION_UPLOADS.pop(sid, None)
                loop.call_soon_threadsafe(queue.put_nowait, ("__end__", {}))

        loop.run_in_executor(None, worker)
        yield _sse("open", {"session_id": sid})

        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=12)
            except asyncio.TimeoutError:
                # A stage like "research" or "verify" can legitimately run well
                # past a minute with zero bytes sent. Proxies in front of the
                # server (Render's Cloudflare edge included) treat a silent
                # connection as dead and kill it with a 502 — this comment
                # line keeps traffic flowing without affecting the client's
                # named-event parsing.
                yield ": keep-alive\n\n"
                continue
            if event == "__end__":
                break
            yield _sse(event, data)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ audit


@app.get("/api/runs")
def runs(limit: int = 25, user: dict = Depends(require_user)):
    return {"runs": audit.list_runs(limit)}


@app.get("/api/runs/{session_id}")
def run_detail(session_id: str, user: dict = Depends(require_user)):
    doc = audit.get_run(session_id)
    if not doc:
        raise HTTPException(404, "No stored run with that session id.")
    return doc


@app.post("/api/runs/{session_id}/review")
def review(
    session_id: str,
    reviewer: str = Form(...),
    decision: str = Form(...),
    note: str = Form(""),
    user: dict = Depends(require_user),
):
    if decision not in ("approved", "rejected", "needs_edit"):
        raise HTTPException(400, "decision must be approved, rejected, or needs_edit")
    ok = audit.record_review(session_id, reviewer, decision, note)
    if not ok:
        raise HTTPException(404, "No stored run with that session id.")
    return {"session_id": session_id, "decision": decision}
