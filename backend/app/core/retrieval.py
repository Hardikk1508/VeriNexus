"""Hybrid evidence store: dense (Chroma + MiniLM) + sparse (BM25), fused with RRF.

Dense-only retrieval reliably misses exact entities - names, figures, dates, statute
numbers - which are precisely the tokens a verification step must check. Hence the fusion.
"""

import logging
import re
import threading
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.schemas import Chunk

log = logging.getLogger("verinexus.retrieval")

_model = None
_model_lock = threading.Lock()


def embedder():
    """Lazy singleton — the model is ~90 MB and must not load per request."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                log.info("loading embedding model %s", settings.EMBED_MODEL)
                _model = SentenceTransformer(settings.EMBED_MODEL)
    return _model


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def rrf_fuse(rankings: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion.

    score(d) = sum over rankers of 1 / (k + rank(d))

    Used instead of score normalisation because BM25 scores and cosine similarities
    live on incomparable scales; RRF only needs ranks.
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class EvidenceStore:
    """Per-session evidence pool. In-memory Chroma + in-memory BM25."""

    def __init__(self, session_id: str):
        import chromadb

        self.session_id = session_id
        self._client = chromadb.EphemeralClient()
        self._col = self._client.create_collection(
            name=f"ev_{session_id[:40]}", metadata={"hnsw:space": "cosine"}
        )
        self._chunks: Dict[str, Chunk] = {}
        self._order: List[str] = []
        self._bm25 = None
        self._bm25_dirty = True

    # ------------------------------------------------------------ writes

    def add(self, chunks: List[Chunk]) -> int:
        """Add chunks, skipping ones already present. Returns number actually added."""
        fresh = [c for c in chunks if c.chunk_id not in self._chunks]
        if not fresh:
            return 0

        vectors = embedder().encode(
            [c.text for c in fresh], normalize_embeddings=True, show_progress_bar=False
        )
        self._col.add(
            ids=[c.chunk_id for c in fresh],
            embeddings=[v.tolist() for v in vectors],
            documents=[c.text for c in fresh],
            metadatas=[
                {
                    "source_id": c.source_id,
                    "domain": c.domain,
                    "title": c.title,
                    "url": c.url,
                    "kind": c.kind,
                }
                for c in fresh
            ],
        )
        for c in fresh:
            self._chunks[c.chunk_id] = c
            self._order.append(c.chunk_id)
        self._bm25_dirty = True
        return len(fresh)

    # ------------------------------------------------------------ reads

    def _ensure_bm25(self):
        if not self._bm25_dirty and self._bm25 is not None:
            return
        from rank_bm25 import BM25Okapi

        corpus = [_tokenize(self._chunks[cid].text) for cid in self._order]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._bm25_dirty = False

    def _dense(self, query: str, k: int) -> List[str]:
        if not self._order:
            return []
        vec = embedder().encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        res = self._col.query(
            query_embeddings=[vec.tolist()], n_results=min(k, len(self._order))
        )
        return list(res.get("ids", [[]])[0])

    def _sparse(self, query: str, k: int) -> List[str]:
        self._ensure_bm25()
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._order[i] for i in ranked[:k] if scores[i] > 0]

    def search(self, query: str, k: int = 5, pool: int = 20) -> List[Chunk]:
        """Hybrid search. Returns up to k chunks, best first."""
        if not self._order:
            return []
        dense = self._dense(query, pool)
        sparse = self._sparse(query, pool)
        fused = rrf_fuse([dense, sparse], k=settings.RRF_K)
        return [self._chunks[cid] for cid, _ in fused[:k] if cid in self._chunks]

    def search_diverse(self, query: str, k: int = 5, per_domain: int = 2) -> List[Chunk]:
        """Hybrid search with a per-domain cap.

        Without this cap one verbose source floods every claim's evidence set and the
        agreement signal collapses to a constant.
        """
        candidates = self.search(query, k=k * 4, pool=40)
        seen: Dict[str, int] = {}
        out: List[Chunk] = []
        for c in candidates:
            if seen.get(c.domain, 0) >= per_domain:
                continue
            seen[c.domain] = seen.get(c.domain, 0) + 1
            out.append(c)
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------ info

    def get(self, chunk_id: str) -> Optional[Chunk]:
        return self._chunks.get(chunk_id)

    def all_chunks(self) -> List[Chunk]:
        return [self._chunks[cid] for cid in self._order]

    @property
    def size(self) -> int:
        return len(self._order)

    @property
    def domains(self) -> set:
        return {c.domain for c in self._chunks.values()}
