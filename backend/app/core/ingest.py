"""Evidence acquisition: web search, PDF extraction, chunking."""

import hashlib
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.core.scoring import domain_of, source_authority
from app.schemas import Chunk, Source

log = logging.getLogger("verinexus.ingest")

_tavily = None


def tavily():
    global _tavily
    if _tavily is None:
        from tavily import TavilyClient

        if not settings.TAVILY_API_KEY:
            raise RuntimeError("TAVILY_API_KEY is not set.")
        _tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
    return _tavily


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]


_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    text = _WS_RE.sub(" ", text or "")
    text = _NL_RE.sub("\n\n", text)
    return text.strip()


# ------------------------------------------------------------------ chunking


def chunk_text(
    text: str,
    source: Source,
    words: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[Chunk]:
    """Sliding word-window chunking with overlap.

    Word windows rather than sentence splitting: verification needs chunks that are
    long enough to carry a full premise, and sentence splitters fragment numeric and
    tabular content badly.
    """
    words = words or settings.CHUNK_WORDS
    overlap = overlap or settings.CHUNK_OVERLAP
    tokens = clean(text).split()
    if not tokens:
        return []

    step = max(words - overlap, 1)
    chunks: List[Chunk] = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + words]
        if len(window) < 25 and chunks:
            break
        body = " ".join(window)
        chunks.append(
            Chunk(
                chunk_id=f"{source.source_id}:{start}:{_hash(body)}",
                text=body,
                source_id=source.source_id,
                title=source.title,
                url=source.url,
                domain=source.domain,
                kind=source.kind,
                offset=start,
            )
        )
        if start + words >= len(tokens):
            break
    return chunks


# ------------------------------------------------------------------ web


def web_search(
    queries: List[str], per_query: Optional[int] = None, deep: bool = False
) -> Tuple[List[Source], List[Chunk]]:
    """Run searches and return (sources, chunks), deduplicated by URL."""
    per_query = per_query or settings.RESULTS_PER_QUERY
    seen_urls: Dict[str, Source] = {}
    all_chunks: List[Chunk] = []
    cli = tavily()

    for q in queries:
        try:
            res = cli.search(
                query=q,
                max_results=per_query,
                search_depth="advanced" if deep else "basic",
                include_raw_content=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("tavily search failed for %r: %s", q, exc)
            continue

        for item in res.get("results", []):
            url = item.get("url") or ""
            if not url or url in seen_urls:
                continue
            body = item.get("raw_content") or item.get("content") or ""
            body = clean(body)
            if len(body.split()) < 40:
                continue

            dom = domain_of(url)
            src = Source(
                source_id=f"S{_hash(url)}",
                title=clean(item.get("title") or dom)[:200],
                url=url,
                domain=dom,
                kind="web",
                authority=source_authority(dom, "web"),
            )
            # cap very long pages so one source cannot dominate the pool
            words = body.split()
            if len(words) > 4000:
                body = " ".join(words[:4000])

            ch = chunk_text(body, src)
            if not ch:
                continue
            src.chunk_count = len(ch)
            seen_urls[url] = src
            all_chunks.extend(ch)

    return list(seen_urls.values()), all_chunks


# ------------------------------------------------------------------ pdf


def pdf_to_source(path: str, display_name: str) -> Tuple[Source, List[Chunk]]:
    """Extract a PDF into a Source plus its chunks. Page numbers preserved in offsets."""
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc):
        txt = clean(page.get_text("text"))
        if txt:
            pages.append(f"[page {i + 1}] {txt}")
    doc.close()

    body = "\n\n".join(pages)
    src = Source(
        source_id=f"U{_hash(display_name + str(len(body)))}",
        title=display_name,
        url=f"upload://{display_name}",
        domain=f"upload/{display_name}",
        kind="upload",
        authority=source_authority("", "upload"),
    )
    chunks = chunk_text(body, src)
    src.chunk_count = len(chunks)
    return src, chunks
