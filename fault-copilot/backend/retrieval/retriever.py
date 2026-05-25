"""Hybrid retrieval: semantic + BM25 keyword + cross-encoder re-ranking."""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from typing import Optional

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import chromadb  # noqa: E402

from config import (  # noqa: E402
    CHROMA_PATH,
    EMBEDDING_MODEL,
    RERANKER_MODEL,
    USE_BM25,
    USE_RERANKER,
)
from embeddings import get_embedding_function  # noqa: E402

# Matches bare fault codes like "E-47", "F023", "ERR_4"
_FAULT_CODE_RE = re.compile(r"^[A-Za-z]{1,8}[-_]?\d+$")


# ── Singletons ─────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _get_client(path: str) -> chromadb.PersistentClient:
    """Singleton PersistentClient — one filesystem open per process."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=path)


@functools.lru_cache(maxsize=None)
def _get_bm25_index(manual_id: str, chroma_path: str) -> Optional[dict]:
    """Build a BM25 index from an existing ChromaDB collection, then cache it.

    Fetches all documents from the collection once; subsequent calls use the
    cached result.  The cache is invalidated automatically on process restart
    (e.g. after --force re-ingestion, which restarts the server).
    """
    if not USE_BM25:
        return None
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        print("[warn] rank_bm25 not installed — BM25 disabled", file=sys.stderr)
        return None

    try:
        client = _get_client(chroma_path)
        ef = get_embedding_function(EMBEDDING_MODEL)
        kwargs: dict = {"name": f"manual_{manual_id}"}
        if ef is not None:
            kwargs["embedding_function"] = ef
        collection = client.get_collection(**kwargs)
        data = collection.get()
        docs = data.get("documents") or []
        if not docs:
            return None
        metas = data.get("metadatas") or [{}] * len(docs)
        ids   = data.get("ids")       or [""] * len(docs)
        tokenized = [d.lower().split() for d in docs]
        print(f"[info] BM25 index built for '{manual_id}' ({len(docs)} chunks).", file=sys.stderr)
        return {
            "bm25":      BM25Okapi(tokenized),
            "documents": docs,
            "metadatas": metas,
            "ids":       ids,
            "manual_id": manual_id,
        }
    except Exception as exc:
        print(f"[warn] BM25 index build failed for '{manual_id}': {exc}", file=sys.stderr)
        return None


# Cross-encoder is loaded lazily at first retrieval call
_CE_MODEL = None
_CE_LOADED = False


def _get_cross_encoder():
    global _CE_MODEL, _CE_LOADED
    if _CE_LOADED:
        return _CE_MODEL
    _CE_LOADED = True
    if not USE_RERANKER:
        return None
    try:
        from sentence_transformers.cross_encoder import CrossEncoder
        print(
            f"[info] Loading cross-encoder: {RERANKER_MODEL}"
            " (first call — may download ~22 MB) …",
            file=sys.stderr,
        )
        _CE_MODEL = CrossEncoder(RERANKER_MODEL, max_length=512)
        print("[info] Cross-encoder ready.", file=sys.stderr)
    except Exception as exc:
        print(f"[warn] Cross-encoder not available: {exc}", file=sys.stderr)
    return _CE_MODEL


# ── Per-query helpers ──────────────────────────────────────────────────────────

def _distance_to_score(distance: float) -> float:
    return round(max(0.0, 1.0 - distance), 4)


def _query_collection(
    collection: "chromadb.Collection",
    query: str,
    n: int,
    where_document: Optional[dict] = None,
) -> list[dict]:
    kwargs: dict = {"query_texts": [query], "n_results": n}
    if where_document is not None:
        kwargs["where_document"] = where_document
    try:
        resp = collection.query(**kwargs)
    except Exception as exc:
        print(
            f"  [warn] query failed on '{collection.name}'"
            f" (where_document={where_document}): {exc}",
            file=sys.stderr,
        )
        return []

    docs  = resp.get("documents",  [[]])[0] or []
    metas = resp.get("metadatas",  [[]])[0] or []
    dists = resp.get("distances",  [[]])[0] or []

    return [
        {
            "content":         doc,
            "section_heading": (meta or {}).get("section_heading", ""),
            "page_num":        (meta or {}).get("page_num"),
            "manual_id":       (meta or {}).get("manual_id", ""),
            "relevance_score": _distance_to_score(dist),
            "match_type":      "exact" if where_document else "semantic",
        }
        for doc, meta, dist in zip(docs, metas, dists)
    ]


def _bm25_search(query: str, idx: dict, n: int) -> list[dict]:
    """Return top-n BM25 results for query against a pre-built index."""
    scores    = idx["bm25"].get_scores(query.lower().split())
    top_idxs  = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
    results: list[dict] = []
    for rank, i in enumerate(top_idxs):
        if scores[i] <= 0:
            break
        meta = idx["metadatas"][i] or {}
        results.append({
            "content":         idx["documents"][i],
            "section_heading": meta.get("section_heading", ""),
            "page_num":        meta.get("page_num"),
            "manual_id":       idx["manual_id"],
            "relevance_score": 0.0,    # overwritten by RRF / cross-encoder
            "match_type":      "keyword",
            "_bm25_score":     float(scores[i]),
            "_bm25_rank":      rank,
        })
    return results


def _rrf_merge(
    sem:  list[dict],
    kw:   list[dict],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion of semantic and keyword-ranked lists."""
    sem_rank = {r["content"]: i for i, r in enumerate(sem)}
    kw_rank  = {r["content"]: i for i, r in enumerate(kw)}

    all_docs: dict[str, dict] = {}
    for r in sem + kw:
        if r["content"] not in all_docs:
            all_docs[r["content"]] = r.copy()

    n_sem = len(sem)
    n_kw  = len(kw)
    for content, doc in all_docs.items():
        sr = sem_rank.get(content, n_sem + 1000)
        kr = kw_rank.get(content,  n_kw  + 1000)
        doc["_rrf_score"] = 1.0 / (k + sr) + 1.0 / (k + kr)

    return sorted(all_docs.values(), key=lambda d: d["_rrf_score"], reverse=True)[:top_k]


# ── Public API ─────────────────────────────────────────────────────────────────

def retrieve_context(
    query: str,
    manual_ids: list[str],
    n_results: int = 5,
) -> list[dict]:
    """Retrieve the most relevant chunks for a fault query.

    Pipeline
    --------
    1. Exact fault-code filter (if query looks like a code)
    2. Semantic search via ChromaDB (BAAI/bge-small-en-v1.5 embeddings)
    3. BM25 keyword search (catches exact technical-term matches)
    4. Reciprocal Rank Fusion of semantic + BM25
    5. Cross-encoder re-ranking of the merged candidate set
    """
    if not manual_ids:
        return []

    chroma_client = _get_client(str(CHROMA_PATH))
    ef            = get_embedding_function(EMBEDDING_MODEL)

    fault_code: Optional[str] = None
    if _FAULT_CODE_RE.match(query.strip()):
        fault_code = query.strip()

    # Fetch more candidates when re-ranking will be applied
    fetch_n = n_results * 3 if USE_RERANKER else n_results

    all_candidates: list[dict] = []
    seen: set[str] = set()

    for manual_id in manual_ids:
        coll_name = f"manual_{manual_id}"
        try:
            coll_kwargs: dict = {"name": coll_name}
            if ef is not None:
                coll_kwargs["embedding_function"] = ef
            collection = chroma_client.get_collection(**coll_kwargs)
        except Exception:
            print(f"[warn] collection '{coll_name}' not found, skipping", file=sys.stderr)
            continue

        total = collection.count()
        if total == 0:
            continue
        n = min(fetch_n, total)

        # --- Exact fault-code match (mark_type = "exact") ---
        if fault_code:
            for row in _query_collection(
                collection, query, n,
                where_document={"$contains": fault_code},
            ):
                if row["content"] not in seen:
                    seen.add(row["content"])
                    all_candidates.append(row)

        # --- Semantic search ---
        sem = _query_collection(collection, query, n)

        # --- BM25 keyword search ---
        kw: list[dict] = []
        if USE_BM25:
            idx = _get_bm25_index(manual_id, str(CHROMA_PATH))
            if idx:
                kw = _bm25_search(query, idx, n)

        # --- RRF merge semantic + BM25 ---
        merged = _rrf_merge(sem, kw, fetch_n) if kw else sem

        for row in merged:
            if row["content"] not in seen:
                seen.add(row["content"])
                all_candidates.append(row)

    if not all_candidates:
        return []

    # --- Cross-encoder re-ranking ---
    ce = _get_cross_encoder()
    if ce is not None and all_candidates:
        pairs = [(query, c["content"]) for c in all_candidates]
        try:
            raw_scores = ce.predict(pairs, show_progress_bar=False)
            lo, hi = min(raw_scores), max(raw_scores)
            span = hi - lo if hi > lo else 1.0
            for c, s in zip(all_candidates, raw_scores):
                c["relevance_score"] = round((float(s) - lo) / span, 4)
            all_candidates.sort(key=lambda c: c["relevance_score"], reverse=True)
        except Exception as exc:
            print(f"[warn] Cross-encoder prediction failed: {exc}", file=sys.stderr)
            # Fallback: RRF score then semantic score
            all_candidates.sort(
                key=lambda c: c.get("_rrf_score", c["relevance_score"]),
                reverse=True,
            )
    else:
        # No re-ranker: exact matches first, then by RRF/semantic score
        exact = [c for c in all_candidates if c["match_type"] == "exact"]
        rest  = sorted(
            [c for c in all_candidates if c["match_type"] != "exact"],
            key=lambda c: c.get("_rrf_score", c["relevance_score"]),
            reverse=True,
        )
        all_candidates = exact + rest

    return all_candidates[:n_results]


def list_manuals() -> list[dict]:
    chroma_client = _get_client(str(CHROMA_PATH))
    try:
        collections = chroma_client.list_collections()
    except Exception as exc:
        print(f"[error] failed to list ChromaDB collections: {exc}", file=sys.stderr)
        return []

    manuals: list[dict] = []
    for col in collections:
        if not col.name.startswith("manual_"):
            continue
        manual_id = col.name[len("manual_"):]
        try:
            count        = col.count()
            date_ingested = (col.metadata or {}).get("date_ingested")
        except Exception as exc:
            print(f"  [warn] failed to read '{col.name}': {exc}", file=sys.stderr)
            count = None
            date_ingested = None
        manuals.append({"manual_id": manual_id, "chunk_count": count, "date_ingested": date_ingested})
    return manuals


def manual_exists(manual_id: str) -> bool:
    chroma_client = _get_client(str(CHROMA_PATH))
    try:
        chroma_client.get_collection(f"manual_{manual_id}")
        return True
    except Exception:
        return False
