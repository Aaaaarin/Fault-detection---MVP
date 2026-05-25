"""FastAPI application entry point for the fault resolution copilot."""

from __future__ import annotations

# stdlib logging must be imported before backend/ is added to sys.path.
# backend/logging/__init__.py (legacy name) would otherwise shadow it.
import logging as _logging_stdlib  # noqa: F401  — side-effect: caches in sys.modules

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

# ---------------------------------------------------------------------------
# sys.path setup — must happen before any local imports.
#
# backend/ is added so subpackages (ingestion, retrieval, resolution) resolve.
#
# backend/fault_logging/ is added as a flat entry so fault_logger.py is
# importable as `fault_logger` directly (avoids any name collision with stdlib).
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent
for _p in (_BACKEND_DIR, _BACKEND_DIR / "fault_logging"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import anthropic  # noqa: E402
from fastapi import FastAPI, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from config import ANTHROPIC_API_KEY  # noqa: E402
from fault_logger import (  # noqa: E402  (sourced from backend/logging/)
    get_fault_frequency,
    get_recent_faults,
    init_db,
    log_fault_start,
    log_resolution_complete,
)
from ingestion.embedder import ingest_manual  # noqa: E402
from resolution.resolver import resolve_fault  # noqa: E402
from retrieval.retriever import list_manuals  # noqa: E402


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

_anthropic_client: Optional[anthropic.Anthropic] = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _anthropic_client
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in environment — cannot start."
        )
    _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    init_db()
    yield
    # Nothing to tear down; SQLite connections are per-call.


app = FastAPI(
    title="Fault Resolution Copilot",
    description=(
        "AI-powered fault resolution for industrial packaging equipment. "
        "Retrieve manual context, generate step-by-step guidance, log outcomes."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ResolveRequest(BaseModel):
    fault_input: str
    manual_ids: list[str]
    operator_level: str = "operator"   # "operator" | "technician"
    plant_id: Optional[str] = None


class CompleteRequest(BaseModel):
    accepted: bool
    time_seconds: int
    notes: Optional[str] = None


class IngestRequest(BaseModel):
    manual_path: str
    manual_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message: str, status: int = 500) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/resolve", response_model=None)
def post_resolve(req: ResolveRequest):
    """Generate step-by-step resolution for a reported fault.

    Retrieves relevant manual sections, calls Claude to produce a procedure,
    logs the event to SQLite, and returns the structured steps plus a fault_id
    that the client uses to later report the outcome via POST /complete/{id}.
    """
    try:
        resolution = resolve_fault(
            fault_input=req.fault_input,
            manual_ids=req.manual_ids,
            operator_level=req.operator_level,
            client=_anthropic_client,
        )
    except Exception as exc:
        print(f"[error] resolve_fault: {exc}", file=sys.stderr)
        return _error(f"Resolution failed: {exc}")

    try:
        fault_id = log_fault_start(
            fault_input=req.fault_input,
            manual_ids=req.manual_ids,
            operator_level=req.operator_level,
            confidence=resolution.get("confidence", "none"),
            steps_generated=len(resolution.get("steps", [])),
            manual_references=resolution.get("manual_references", []),
            plant_id=req.plant_id,
        )
    except Exception as exc:
        print(f"[warn] log_fault_start failed (fault still returned): {exc}", file=sys.stderr)
        fault_id = None

    # Build source_chunks: deduplicated retrieved context with provenance fields.
    # Operators and managers use this to verify every answer came from the manual.
    seen_keys: set[tuple] = set()
    source_chunks: list[dict] = []
    for chunk in resolution.get("retrieved_context", []):
        key = (chunk.get("section_heading") or "", chunk.get("page_num"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        source_chunks.append({
            "section_heading": chunk.get("section_heading") or "—",
            "page_num":        chunk.get("page_num"),
            "match_type":      chunk.get("match_type", "semantic"),
            "relevance_score": round(chunk.get("relevance_score", 0), 3),
        })

    return {
        "fault_id":         fault_id,
        "steps":            resolution.get("steps", []),
        "confidence":       resolution.get("confidence", "none"),
        "manual_references": resolution.get("manual_references", []),
        "source_chunks":    source_chunks,
        "message":          resolution.get("message"),
    }


@app.post("/complete/{fault_id}", response_model=None)
def post_complete(fault_id: int, req: CompleteRequest):
    """Record the operator's outcome for a previously logged fault."""
    try:
        log_resolution_complete(
            fault_id=fault_id,
            accepted=req.accepted,
            time_seconds=req.time_seconds,
            notes=req.notes,
        )
        return {"success": True}
    except Exception as exc:
        print(f"[error] log_resolution_complete: {exc}", file=sys.stderr)
        return _error(f"Could not update fault record: {exc}")


@app.post("/ingest", response_model=None)
def post_ingest(req: IngestRequest):
    """Parse a PDF manual, describe images, chunk, embed, and store in ChromaDB.

    This call is synchronous and may take several minutes for large manuals
    with many diagrams.  Run it once per manual before using /resolve.
    """
    try:
        chunks = ingest_manual(req.manual_path, req.manual_id, _anthropic_client)
        return {"chunks_stored": chunks, "manual_id": req.manual_id}
    except FileNotFoundError as exc:
        return _error(str(exc), status=404)
    except Exception as exc:
        print(f"[error] ingest_manual: {exc}", file=sys.stderr)
        return _error(f"Ingestion failed: {exc}")


@app.get("/manuals", response_model=None)
def get_manuals():
    """List all ingested manuals available for retrieval."""
    try:
        return list_manuals()
    except Exception as exc:
        print(f"[error] list_manuals: {exc}", file=sys.stderr)
        return _error(f"Could not list manuals: {exc}")


@app.get("/analytics", response_model=None)
def get_analytics(
    plant_id: Optional[str] = Query(default=None, description="Filter by plant ID"),
):
    """Return fault frequency data and recent fault history.

    fault_frequency is sorted most-frequent-first and includes avg resolution
    time — intended as the primary analytics dashboard data source.
    """
    try:
        return {
            "fault_frequency": get_fault_frequency(),
            "recent_faults": get_recent_faults(limit=20, plant_id=plant_id),
        }
    except Exception as exc:
        print(f"[error] analytics: {exc}", file=sys.stderr)
        return _error(f"Could not retrieve analytics: {exc}")


@app.get("/health", response_model=None)
def get_health():
    """Liveness + readiness check. Includes demo config for the frontend."""
    try:
        manuals = list_manuals()
        freq = get_fault_frequency()
        total_faults = sum(int(f.get("count", 0)) for f in freq)
        demo_mode = os.getenv("DEMO_MODE", "false").lower() in ("1", "true", "yes")
        return {
            "status":             "ok",
            "manuals_loaded":     len(manuals),
            "manuals":            [m["manual_id"] for m in manuals],
            "total_faults_logged": total_faults,
            "api_key_set":        bool(ANTHROPIC_API_KEY),
            "demo_mode":          demo_mode,
            "plant_name":         os.getenv("PLANT_NAME", "Demo Plant"),
        }
    except Exception as exc:
        print(f"[error] health check: {exc}", file=sys.stderr)
        return _error(f"Health check failed: {exc}", status=503)


# ---------------------------------------------------------------------------
# Dev server entry point
# Run with: uvicorn main:app --reload --port 8000
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
