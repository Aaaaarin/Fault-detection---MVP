"""Shared embedding function factory for ChromaDB collections.

Centralises the embedding-model choice so ingestion and retrieval always use
the same function, avoiding the silent embedding-space mismatch that would
otherwise produce garbage similarity scores.
"""
from __future__ import annotations

import functools
import sys


@functools.lru_cache(maxsize=4)
def get_embedding_function(model_name: str):
    """Return a ChromaDB-compatible SentenceTransformer embedding function.

    Cached per model name — expensive model loading happens once per process.
    The model is downloaded from HuggingFace on first use and cached locally
    by sentence-transformers in ~/.cache/torch/sentence_transformers/.

    Returns None if model_name is empty, which makes ChromaDB fall back to its
    built-in default (all-MiniLM-L6-v2).  Changing the model requires --force
    re-ingestion of all collections.
    """
    if not model_name:
        return None
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        print(
            f"[info] Loading embedding model: {model_name} (CPU, first call may download ~90 MB) …",
            file=sys.stderr,
        )
        ef = SentenceTransformerEmbeddingFunction(model_name=model_name, device="cpu")
        print("[info] Embedding model ready.", file=sys.stderr)
        return ef
    except Exception as exc:
        print(f"[warn] Could not load embedding model '{model_name}': {exc}", file=sys.stderr)
        return None
