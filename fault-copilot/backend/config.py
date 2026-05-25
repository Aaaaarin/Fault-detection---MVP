"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-4-5")
RESOLVER_MODEL = os.getenv("RESOLVER_MODEL", "claude-sonnet-4-6")

CHROMA_PATH = Path(os.getenv("CHROMA_PATH", "./data/chroma"))
MANUALS_PATH = Path(os.getenv("MANUALS_PATH", "./manuals"))
LOG_DB_PATH = Path(os.getenv("LOG_DB_PATH", "./logs/faults.db"))

# Confidence scoring thresholds (1A — score-based, not fault-code-dependent)
CONFIDENCE_MEDIUM_THRESHOLD: float = float(os.getenv("CONFIDENCE_MEDIUM_THRESHOLD", "0.35"))
CONFIDENCE_HIGH_THRESHOLD:   float = float(os.getenv("CONFIDENCE_HIGH_THRESHOLD",   "0.40"))

# In-process resolution cache — 0 to disable
RESOLVER_CACHE_SIZE: int   = int(os.getenv("RESOLVER_CACHE_SIZE", "128"))
RESOLVER_CACHE_TTL:  float = float(os.getenv("RESOLVER_CACHE_TTL", "300"))

# Embedding model for ChromaDB (2B / 3B).
# BAAI/bge-small-en-v1.5 is retrieval-tuned, same 384-dim as the default,
# and significantly better on technical vocabulary.
# Changing this requires --force re-ingestion of all manuals.
# Set to "" to use ChromaDB's built-in default (all-MiniLM-L6-v2).
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# BM25 keyword hybrid retrieval (2B) — combines with semantic via RRF
USE_BM25: bool = os.getenv("USE_BM25", "true").lower() in ("1", "true", "yes")

# Cross-encoder re-ranking (3A) — second-stage precision boost, CPU-only ~22 MB model
USE_RERANKER:    bool = os.getenv("USE_RERANKER",    "true").lower() in ("1", "true", "yes")
RERANKER_MODEL:  str  = os.getenv("RERANKER_MODEL",  "cross-encoder/ms-marco-MiniLM-L-6-v2")
