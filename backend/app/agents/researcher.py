"""Research Agent — fills the evidence pool from the web and from uploaded documents."""

import logging
from typing import Any, Dict, List, Tuple

from app.core.ingest import pdf_to_source, web_search
from app.core.retrieval import EvidenceStore
from app.schemas import Source

log = logging.getLogger("verinexus.researcher")


def research(
    store: EvidenceStore,
    queries: List[str],
    deep: bool = False,
) -> Tuple[List[Source], int]:
    """Run web searches and add results to the store. Returns (new_sources, chunks_added)."""
    if not queries:
        return [], 0
    try:
        sources, chunks = web_search(queries, deep=deep)
    except Exception as exc:  # noqa: BLE001
        log.error("web research failed: %s", exc)
        return [], 0

    added = store.add(chunks)
    log.info("research: %s queries -> %s sources, %s chunks", len(queries), len(sources), added)
    return sources, added


def load_uploads(
    store: EvidenceStore, uploads: List[Dict[str, str]]
) -> Tuple[List[Source], int]:
    """uploads: [{"path": "/tmp/x.pdf", "name": "syllabus.pdf"}]"""
    sources, total = [], 0
    for u in uploads:
        try:
            src, chunks = pdf_to_source(u["path"], u.get("name", "document.pdf"))
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to read upload %s: %s", u.get("name"), exc)
            continue
        total += store.add(chunks)
        sources.append(src)
    return sources, total


def merge_sources(
    existing: List[Dict[str, Any]], new: List[Source]
) -> List[Dict[str, Any]]:
    by_id = {s["source_id"]: s for s in existing}
    for s in new:
        by_id.setdefault(s.source_id, s.to_dict())
    return list(by_id.values())
