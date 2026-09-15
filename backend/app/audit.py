"""Audit trail persistence. Degrades to a no-op if MONGODB_URI is not configured,
so the prototype runs with zero external database setup."""

import datetime as _dt
import logging
from typing import Any, Dict, List, Optional

from app.config import settings

log = logging.getLogger("verinexus.audit")

_db = None
_checked = False


def db():
    global _db, _checked
    if _checked:
        return _db
    _checked = True
    if not settings.MONGODB_URI:
        log.info("MONGODB_URI not set — audit trail will not be persisted")
        return None
    try:
        from pymongo import MongoClient

        # serverSelectionTimeoutMS alone isn't enough — each shard connection
        # attempt has its own connect/socket timeout (20s default) that can
        # stack up across a 3-node replica set. Cap all three so a failing
        # connection gives up in ~4s instead of blocking a worker thread for
        # nearly a minute.
        client = MongoClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=4000,
            connectTimeoutMS=4000,
            socketTimeoutMS=4000,
        )
        client.admin.command("ping")
        _db = client[settings.MONGODB_DB]
        _db.runs.create_index("session_id")
        _db.runs.create_index("created_at")
        log.info("audit trail connected to MongoDB")
    except Exception as exc:  # noqa: BLE001
        log.warning("MongoDB unavailable, audit trail disabled: %s", exc)
        _db = None
    return _db


def save_run(payload: Dict[str, Any]) -> Optional[str]:
    d = db()
    if d is None:
        return None
    doc = dict(payload)
    doc["created_at"] = _dt.datetime.utcnow()
    try:
        return str(d.runs.insert_one(doc).inserted_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to persist run: %s", exc)
        return None


def list_runs(limit: int = 25) -> List[Dict[str, Any]]:
    d = db()
    if d is None:
        return []
    cur = d.runs.find(
        {},
        {
            "_id": 0,
            "session_id": 1,
            "query": 1,
            "confidence": 1,
            "band": 1,
            "needs_human_review": 1,
            "created_at": 1,
            "stats": 1,
        },
    ).sort("created_at", -1).limit(limit)
    out = []
    for doc in cur:
        if isinstance(doc.get("created_at"), _dt.datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        out.append(doc)
    return out


def get_run(session_id: str) -> Optional[Dict[str, Any]]:
    d = db()
    if d is None:
        return None
    doc = d.runs.find_one({"session_id": session_id}, {"_id": 0})
    if doc and isinstance(doc.get("created_at"), _dt.datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc


def record_review(session_id: str, reviewer: str, decision: str, note: str = "") -> bool:
    """Human oversight step — the loop is only closed if the decision is written down."""
    d = db()
    if d is None:
        return False
    res = d.runs.update_one(
        {"session_id": session_id},
        {
            "$push": {
                "reviews": {
                    "reviewer": reviewer,
                    "decision": decision,
                    "note": note,
                    "at": _dt.datetime.utcnow(),
                }
            },
            "$set": {"review_status": decision},
        },
    )
    return res.matched_count > 0
