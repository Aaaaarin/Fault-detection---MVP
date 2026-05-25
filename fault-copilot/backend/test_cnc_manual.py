#!/usr/bin/env python3
"""
Self-test for fault-copilot MVP using the Allen-Bradley 9/Series CNC manual.

Run from fault-copilot/ directory:
    python backend/test_cnc_manual.py
    python backend/test_cnc_manual.py --force    # delete + re-ingest
"""

from __future__ import annotations

# stdlib logging must be imported before backend/ lands on sys.path.
# backend/logging/__init__.py (legacy name) shadows it otherwise.
import logging as _logging_stdlib  # noqa: F401  — caches in sys.modules

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── UTF-8 stdout/stderr (Windows cp1252 consoles can't print ═ ✓ ✗ otherwise) ─
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── sys.path setup (must precede all local imports) ──────────────────────────
_SCRIPT_DIR  = Path(__file__).resolve().parent   # fault-copilot/backend/
_PROJECT_DIR = _SCRIPT_DIR.parent               # fault-copilot/
_LOGGING_DIR = _SCRIPT_DIR / "fault_logging"

for _p in (_SCRIPT_DIR, _LOGGING_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Load .env early so config.py sees the key when it imports
try:
    from dotenv import load_dotenv as _ld
    _env_file = _PROJECT_DIR / ".env"
    _ld(_env_file if _env_file.exists() else None, override=False)
except Exception:
    pass

# ── Lazy import block — keeps errors human-readable ──────────────────────────
_import_errors: list[str] = []

try:
    import anthropic as _anthropic_lib
except ImportError as e:
    _import_errors.append(f"anthropic: {e}")

try:
    from config import ANTHROPIC_API_KEY, CHROMA_PATH
except ImportError as e:
    _import_errors.append(f"config: {e}")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CHROMA_PATH = _PROJECT_DIR / "data" / "chroma"

try:
    import chromadb as _chromadb
except ImportError as e:
    _import_errors.append(f"chromadb: {e}")
    _chromadb = None  # type: ignore

try:
    from ingestion.embedder import ingest_manual
except ImportError as e:
    _import_errors.append(f"ingestion.embedder: {e}")
    ingest_manual = None  # type: ignore

try:
    from retrieval.retriever import retrieve_context, manual_exists, list_manuals
except ImportError as e:
    _import_errors.append(f"retrieval.retriever: {e}")
    retrieve_context = manual_exists = list_manuals = None  # type: ignore

try:
    from resolution.resolver import resolve_fault
except ImportError as e:
    _import_errors.append(f"resolution.resolver: {e}")
    resolve_fault = None  # type: ignore

try:
    from fault_logger import (
        init_db, log_fault_start,
        log_resolution_complete, get_recent_faults,
    )
except ImportError as e:
    _import_errors.append(f"fault_logger: {e}")
    init_db = log_fault_start = log_resolution_complete = get_recent_faults = None  # type: ignore


# ── Constants ─────────────────────────────────────────────────────────────────
MANUAL_FILENAME  = "service_manual_PM10010.pdf"
MANUAL_ID        = "pm10010_service_manual"
DEFAULT_MAX_PAGES = 120          # text-only default; ignored when --use-vision
REPORT_PATH      = _PROJECT_DIR / "logs" / "cnc_self_test_report.md"

RETRIEVAL_QUERIES = [
    # Fault table (p22-23)
    "lamp not on turntable not rotating food not heated",
    "fuse broken transformer short circuit",
    # Safety (p2)
    "safety precautions before servicing interlock check",
    # Door components (p5-6, p10-11)
    "door interlock switch latch pilot switch",
    # Magnetron (p12)
    "magnetron antenna wave guide assembly",
    # Fan motor (p13)
    "fan motor assembly shaft glue",
    # Microwave leakage (p19-20)
    "microwave leakage door seal measurement",
    # Capacitor / diode (p14, p18)
    "capacitor discharge diode polarity transformer resistance",
]

RESOLUTION_QUERIES = [
    # Directly from the fault table on p22
    "The oven lamp is off, the turntable is not rotating, and the food is not heated. What should I check?",
    "There is sparking inside the oven cavity. What are the possible causes and how do I fix it?",
    "How do I safely discharge the capacitor before servicing the microwave?",
    "The door will not open. What are the causes and how do I repair it?",
    "How do I check for microwave leakage around the door seal?",
]

# ── ANSI colour helpers ───────────────────────────────────────────────────────
_USE_ANSI = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_ANSI else text

def green(t: str) -> str:  return _c("92", t)
def red(t: str) -> str:    return _c("91", t)
def yellow(t: str) -> str: return _c("93", t)
def cyan(t: str) -> str:   return _c("96", t)
def bold(t: str) -> str:   return _c("1", t)
def dim(t: str) -> str:    return _c("2", t)

TICK = green("✓")
CROSS = red("✗")
SKIP = yellow("⊘")
WARN = yellow("⚠")

def hr(char: str = "─", width: int = 62) -> None:
    print(char * width)

def section_header(n: int, title: str) -> None:
    print()
    hr("═")
    print(bold(f"  [{n}/6] {title}"))
    hr("═")


# ── Manual path finder ────────────────────────────────────────────────────────
def find_manual(explicit_path: Optional[str] = None) -> Optional[Path]:
    """Return the resolved manual path.

    If explicit_path is given (from --manual), search for it relative to CWD
    and project root before trying it as-is.  Otherwise search default locations
    for the current MANUAL_FILENAME.
    """
    if explicit_path:
        for base in (Path.cwd(), _PROJECT_DIR, Path(".")):
            candidate = (base / explicit_path).resolve()
            if candidate.exists():
                return candidate
        # Maybe it's already absolute or cwd-relative and exists directly
        direct = Path(explicit_path).resolve()
        return direct if direct.exists() else None

    candidates = [
        _PROJECT_DIR / "manuals" / MANUAL_FILENAME,
        _PROJECT_DIR.parent / "manuals" / MANUAL_FILENAME,
        Path.cwd() / "manuals" / MANUAL_FILENAME,
        _SCRIPT_DIR / ".." / "manuals" / MANUAL_FILENAME,
        _SCRIPT_DIR / ".." / ".." / "manuals" / MANUAL_FILENAME,
    ]
    for p in candidates:
        resolved = p.resolve()
        if resolved.exists():
            return resolved
    return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Environment
# ══════════════════════════════════════════════════════════════════════════════
def check_environment(explicit_manual_path: Optional[str] = None) -> dict:
    result = {"passed": True, "details": [], "errors": []}

    # Import errors
    # Verify logging resolves to stdlib, not backend/logging/
    import logging as _logging_check
    logging_file = getattr(_logging_check, "__file__", "unknown")
    print(f"  logging.__file__ = {logging_file}")
    if "fault-copilot" in logging_file.replace("\\", "/") or "backend" in logging_file.replace("\\", "/"):
        msg = f"logging is resolving to project path, not stdlib: {logging_file}"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
    else:
        print(f"  {TICK} logging resolves to Python stdlib")
        result["details"].append(f"logging stdlib confirmed: {logging_file}")

    if _import_errors:
        for err in _import_errors:
            msg = f"Import failed — {err}"
            print(f"  {CROSS} {msg}")
            result["errors"].append(msg)
            result["passed"] = False
        print(f"\n  {WARN} Run: pip install -r requirements.txt")
        return result

    print(f"  {TICK} All local modules imported")
    result["details"].append("All local modules imported successfully")

    # .env / API key
    env_path = _PROJECT_DIR / ".env"
    if env_path.exists():
        print(f"  {TICK} .env file found at {env_path}")
        result["details"].append(f".env found at {env_path}")
    else:
        msg = ".env not found — relying on environment variable"
        print(f"  {WARN} {msg}")
        result["details"].append(msg)

    if ANTHROPIC_API_KEY:
        masked = ANTHROPIC_API_KEY[:8] + "..." + ANTHROPIC_API_KEY[-4:]
        print(f"  {TICK} ANTHROPIC_API_KEY is set ({masked})")
        result["details"].append("ANTHROPIC_API_KEY is set")
    else:
        msg = "ANTHROPIC_API_KEY is not set"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False

    # Manual file
    manual_path = find_manual(explicit_manual_path)
    if manual_path:
        size_mb = manual_path.stat().st_size / (1024 * 1024)
        print(f"  {TICK} Manual   : {manual_path.name}  ({size_mb:.1f} MB)")
        print(f"  {TICK} Manual ID: {MANUAL_ID}")
        print(f"       Full path: {manual_path}")
        result["details"].append(f"Manual: {manual_path}  ({size_mb:.1f} MB)")
        result["manual_path"] = str(manual_path)
    else:
        search_name = Path(explicit_manual_path).name if explicit_manual_path else MANUAL_FILENAME
        msg = f"{search_name} not found — checked manuals/ and project root"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Ingestion
# ══════════════════════════════════════════════════════════════════════════════
def check_and_ingest(
    manual_path: str,
    force: bool,
    use_vision: bool = False,
    max_pages: int | None = DEFAULT_MAX_PAGES,
) -> dict:
    result = {"passed": True, "skipped": False, "chunks": 0, "details": [], "errors": []}

    if ingest_manual is None or manual_exists is None:
        msg = "Ingestion modules not importable — skipping"
        print(f"  {WARN} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    already_done = manual_exists(MANUAL_ID)

    if already_done and not force:
        try:
            manuals = list_manuals() if list_manuals else []
            chunks = next((m["chunk_count"] for m in manuals if m["manual_id"] == MANUAL_ID), 0)
        except Exception:
            chunks = "?"
        msg = f"Already ingested ({chunks} chunks).  Use --force to re-ingest."
        print(f"  {SKIP} {msg}")
        result["skipped"] = True
        result["chunks"]  = chunks if isinstance(chunks, int) else 0
        result["details"].append(msg)
        return result

    if already_done and force:
        print(f"  {WARN} --force: deleting existing collection and re-ingesting …")
        try:
            client = _chromadb.PersistentClient(path=str(CHROMA_PATH))
            client.delete_collection(f"manual_{MANUAL_ID}")
            print(f"  {TICK} Deleted collection manual_{MANUAL_ID}")
        except Exception as exc:
            print(f"  {WARN} Could not delete collection: {exc}")

    mode_label = "text+vision (Claude Vision)" if use_vision else "text-only  (no API calls)"
    pages_label = str(max_pages) if max_pages else "all"
    print(f"  Manual ID  : {MANUAL_ID}")
    print(f"  Manual path: {manual_path}")
    print(f"  Mode       : {mode_label}")
    print(f"  Max pages  : {pages_label}")

    if use_vision and not ANTHROPIC_API_KEY:
        msg = "Cannot ingest with vision: ANTHROPIC_API_KEY not set"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    # Vision needs a real client; text-only passes None (never used)
    api_client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY) if use_vision else None

    t0 = time.time()
    try:
        chunks = ingest_manual(
            manual_path, MANUAL_ID, api_client,
            use_vision=use_vision, max_pages=max_pages,
        )
        elapsed = time.time() - t0
        if chunks == 0:
            msg = "Ingestion returned 0 chunks — PDF may be empty or unreadable"
            print(f"  {CROSS} {msg}")
            result["errors"].append(msg)
            result["passed"] = False
        else:
            print(f"  {TICK} Stored {chunks} chunks  ({elapsed:.1f}s)")
            result["details"].append(
                f"Stored {chunks} chunks from {pages_label} pages in {elapsed:.1f}s "
                f"(mode: {'vision' if use_vision else 'text-only'})"
            )
        result["chunks"] = chunks
    except Exception as exc:
        msg = f"Ingestion raised: {exc}"
        print(f"  {CROSS} {msg}")
        traceback.print_exc()
        result["errors"].append(msg)
        result["passed"] = False

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Retrieval
# ══════════════════════════════════════════════════════════════════════════════
def test_retrieval() -> dict:
    result = {"passed": True, "queries": [], "zero_count": 0, "details": [], "errors": []}

    if retrieve_context is None:
        msg = "retrieval module not importable"
        print(f"  {WARN} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    for query in RETRIEVAL_QUERIES:
        row: dict = {"query": query, "chunks": 0, "section": "—", "page": "—", "snippet": ""}
        try:
            hits = retrieve_context(query, [MANUAL_ID], n_results=5)
            row["chunks"] = len(hits)
            if hits:
                top = hits[0]
                row["section"] = top.get("section_heading") or "—"
                row["page"]    = top.get("page_num") or "—"
                row["snippet"] = (top.get("content") or "")[:300]
                status = TICK
            else:
                row["section"] = "(no results)"
                result["zero_count"] += 1
                status = WARN
        except Exception as exc:
            row["error"] = str(exc)
            result["errors"].append(f"'{query}': {exc}")
            result["zero_count"] += 1
            status = CROSS

        result["queries"].append(row)
        print(f"  {status} [{row['chunks']:>2} chunks]  {dim(query)}")
        if row["chunks"] > 0:
            print(f"         Section: {cyan(row['section'])!r}  p.{row['page']}")
            if row.get("snippet"):
                snippet_line = row["snippet"].replace("\n", " ")[:120]
                print(f"         {dim(snippet_line)}…")

    if result["zero_count"] > 3:
        msg = f"{result['zero_count']}/8 queries returned no results (threshold: >3)"
        print(f"\n  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
    else:
        print(f"\n  {TICK} {result['zero_count']}/8 queries returned no results (threshold: ≤3)")
        result["details"].append(
            f"{8 - result['zero_count']}/8 queries returned chunks"
        )

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Full fault resolution
# ══════════════════════════════════════════════════════════════════════════════
def test_resolution() -> dict:
    result = {"passed": True, "queries": [], "usable": 0, "details": [], "errors": []}

    if resolve_fault is None:
        msg = "resolution module not importable"
        print(f"  {WARN} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    if not ANTHROPIC_API_KEY:
        msg = "ANTHROPIC_API_KEY not set — skipping resolution tests"
        print(f"  {WARN} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)

    for i, query in enumerate(RESOLUTION_QUERIES, 1):
        print(f"\n  {bold(f'Query {i}/{len(RESOLUTION_QUERIES)}:')} {query}")
        row: dict = {
            "query": query, "confidence": "none", "steps": 0,
            "first_steps": [], "references": [], "has_safety": False,
            "has_escalate": False, "error": None,
        }
        try:
            t0 = time.time()
            res = resolve_fault(query, [MANUAL_ID], "operator", client)
            elapsed = time.time() - t0

            steps       = res.get("steps", [])
            confidence  = res.get("confidence", "none")
            references  = res.get("manual_references", [])

            row["confidence"]  = confidence
            row["steps"]       = len(steps)
            row["references"]  = references
            row["first_steps"] = [s["instruction"] for s in steps[:3]]
            row["has_safety"]  = any(s.get("safety_critical") for s in steps)
            row["has_escalate"] = any(s.get("escalate") for s in steps)
            row["elapsed"]     = round(elapsed, 1)

            conf_colour = {"high": green, "medium": yellow, "low": red, "none": red}
            cf = conf_colour.get(confidence, red)(confidence.upper())

            if steps:
                result["usable"] += 1
                print(f"    Confidence : {cf}")
                print(f"    Steps      : {len(steps)}  ({elapsed:.1f}s)")
                for j, step in enumerate(steps[:3], 1):
                    safety_tag = f" {red('[SAFETY]')}" if step.get("safety_critical") else ""
                    esc_tag    = f" {yellow('[ESCALATE]')}" if step.get("escalate") else ""
                    print(f"    Step {j}: {step['instruction'][:120]}{safety_tag}{esc_tag}")
                if references:
                    print(f"    Refs       : {', '.join(references[:3])}")
            else:
                msg_text = res.get("message", "no steps generated")
                print(f"    {WARN} {confidence.upper()} — {msg_text}")

        except Exception as exc:
            row["error"] = str(exc)
            result["errors"].append(f"Query {i}: {exc}")
            print(f"    {CROSS} Exception: {exc}")
            traceback.print_exc()

        result["queries"].append(row)

    if result["usable"] == 0:
        msg = "All resolution queries generated zero steps"
        print(f"\n  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
    else:
        print(f"\n  {TICK} {result['usable']}/{len(RESOLUTION_QUERIES)} queries produced usable steps")
        result["details"].append(
            f"{result['usable']}/{len(RESOLUTION_QUERIES)} queries produced usable resolution steps"
        )

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Logging
# ══════════════════════════════════════════════════════════════════════════════
def test_logging() -> dict:
    result = {"passed": True, "fault_id": None, "details": [], "errors": []}

    if any(fn is None for fn in [init_db, log_fault_start, log_resolution_complete, get_recent_faults]):
        msg = "fault_logger module not importable"
        print(f"  {WARN} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    try:
        init_db()
        fault_id = log_fault_start(
            fault_input="SELF-TEST: E-stop reset procedure",
            manual_ids=[MANUAL_ID],
            operator_level="operator",
            confidence="high",
            steps_generated=4,
            manual_references=["Emergency Procedures", "Safety Interlocks"],
            plant_id="TEST_PLANT_01",
        )
        print(f"  {TICK} Logged fault start  → id={fault_id}")
        result["fault_id"] = fault_id
        result["details"].append(f"Logged fault_id={fault_id}")
    except Exception as exc:
        msg = f"log_fault_start failed: {exc}"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    try:
        log_resolution_complete(
            fault_id=fault_id,
            accepted=True,
            time_seconds=185,
            notes="Self-test entry — can be deleted",
        )
        print(f"  {TICK} Marked complete      → accepted=True, time=185s")
        result["details"].append("Resolution logged as complete")
    except Exception as exc:
        msg = f"log_resolution_complete failed: {exc}"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False
        return result

    try:
        recent = get_recent_faults(limit=20)
        found  = any(f.get("id") == fault_id for f in recent)
        if found:
            print(f"  {TICK} Fault id={fault_id} confirmed in get_recent_faults()")
            result["details"].append("get_recent_faults() confirmed the entry")
        else:
            msg = f"Fault id={fault_id} not found in get_recent_faults()"
            print(f"  {CROSS} {msg}")
            result["errors"].append(msg)
            result["passed"] = False
    except Exception as exc:
        msg = f"get_recent_faults failed: {exc}"
        print(f"  {CROSS} {msg}")
        result["errors"].append(msg)
        result["passed"] = False

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Report generation
# ══════════════════════════════════════════════════════════════════════════════
def generate_report(
    env_r: dict,
    ing_r: dict,
    ret_r: dict,
    res_r: dict,
    log_r: dict,
    started_at: datetime,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    sections_passed = [
        env_r["passed"],
        ing_r["passed"] or ing_r.get("skipped", False),
        ret_r["passed"],
        res_r["passed"],
        log_r["passed"],
    ]
    overall = all(sections_passed)
    verdict = "✅ PASS" if overall else "❌ FAIL"

    chunks = ing_r.get("chunks", "unknown")

    lines: list[str] = [
        "# CNC Manual Self-Test Report",
        "",
        f"**Generated:** {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Manual:** `{MANUAL_FILENAME}`  ",
        f"**Manual ID:** `{MANUAL_ID}`  ",
        f"**Chunks ingested:** {chunks}  ",
        "",
        f"## Final Verdict: {verdict}",
        "",
    ]

    # ── Environment ──────────────────────────────────────────────────────────
    env_v = "✅ PASS" if env_r["passed"] else "❌ FAIL"
    lines += [f"## 1. Environment — {env_v}", ""]
    for d in env_r.get("details", []):
        lines.append(f"- {d}")
    for e in env_r.get("errors", []):
        lines.append(f"- ❌ {e}")
    lines.append("")

    # ── Ingestion ─────────────────────────────────────────────────────────────
    if ing_r.get("skipped"):
        ing_v = "⊘ SKIPPED (already ingested)"
    else:
        ing_v = "✅ PASS" if ing_r["passed"] else "❌ FAIL"
    lines += [f"## 2. Ingestion — {ing_v}", ""]
    for d in ing_r.get("details", []):
        lines.append(f"- {d}")
    for e in ing_r.get("errors", []):
        lines.append(f"- ❌ {e}")
    lines.append("")

    # ── Retrieval ─────────────────────────────────────────────────────────────
    ret_v = "✅ PASS" if ret_r["passed"] else "❌ FAIL"
    lines += [f"## 3. Retrieval Tests — {ret_v}", ""]
    lines += [
        "| Query | Chunks | Top Section | Page | Status |",
        "|-------|--------|-------------|------|--------|",
    ]
    for q in ret_r.get("queries", []):
        status = "✅" if q["chunks"] > 0 else "❌"
        section_col = (q.get("section") or "—")[:50]
        lines.append(
            f"| {q['query'][:45]} | {q['chunks']} "
            f"| {section_col} | {q.get('page','—')} | {status} |"
        )
    for e in ret_r.get("errors", []):
        lines.append(f"\n> ❌ {e}")
    lines.append("")

    # ── Resolution ────────────────────────────────────────────────────────────
    res_v = "✅ PASS" if res_r["passed"] else "❌ FAIL"
    lines += [f"## 4. Resolution Tests — {res_v}", ""]
    for i, q in enumerate(res_r.get("queries", []), 1):
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴", "none": "⚫"}.get(
            q["confidence"], "⚫"
        )
        lines.append(f"### {i}. {q['query'][:80]}")
        lines.append(f"- Confidence: {conf_icon} **{q['confidence'].upper()}**")
        lines.append(f"- Steps generated: {q['steps']}")
        lines.append(f"- Safety step present: {'Yes ⚠' if q.get('has_safety') else 'No'}")
        lines.append(f"- Escalate step present: {'Yes 📞' if q.get('has_escalate') else 'No'}")
        if q.get("references"):
            lines.append(f"- Manual refs: {', '.join(q['references'][:4])}")
        if q.get("first_steps"):
            lines.append("- First steps:")
            for j, step in enumerate(q["first_steps"], 1):
                lines.append(f"  {j}. {step[:150]}")
        if q.get("error"):
            lines.append(f"- ❌ Error: {q['error']}")
        lines.append("")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_v = "✅ PASS" if log_r["passed"] else "❌ FAIL"
    lines += [f"## 5. Logging Test — {log_v}", ""]
    for d in log_r.get("details", []):
        lines.append(f"- {d}")
    for e in log_r.get("errors", []):
        lines.append(f"- ❌ {e}")
    lines.append("")

    # ── Recommendations ───────────────────────────────────────────────────────
    lines += ["## Recommendations", ""]
    recs: list[str] = []
    if not env_r["passed"]:
        if any("API_KEY" in e for e in env_r.get("errors", [])):
            recs.append("Set `ANTHROPIC_API_KEY` in your `.env` file (copy `.env.example`).")
        if any("not found" in e.lower() for e in env_r.get("errors", [])):
            recs.append(f"Place `{MANUAL_FILENAME}` in the `manuals/` directory.")
        if any("Import" in e for e in env_r.get("errors", [])):
            recs.append("Run `pip install -r requirements.txt` to install missing dependencies.")
    if not ing_r["passed"] and not ing_r.get("skipped"):
        recs.append(
            "Ingestion returned 0 chunks — verify the PDF is not password-protected "
            "and that PyMuPDF can open it (`import fitz; fitz.open(path)`)."
        )
    if not ret_r["passed"]:
        recs.append(
            f"More than 3 retrieval queries returned no chunks.  "
            f"Re-run ingestion with `--force`.  "
            f"Check that `CHROMA_PATH` ({CHROMA_PATH}) is writable."
        )
    if not res_r["passed"]:
        recs.append(
            "All resolution queries generated zero steps.  "
            "Verify the Anthropic API key is valid and the model name in `config.py` "
            "is a current model (`claude-sonnet-4-6`)."
        )
    if not log_r["passed"]:
        recs.append(
            f"SQLite logging failed.  Check that the `logs/` directory is writable "
            f"and that `fault_logger.py` imports correctly."
        )
    if not recs:
        recs.append("None — all tests passed. 🎉")
    for rec in recs:
        lines.append(f"- {rec}")
    lines.append("")

    report_text = "\n".join(lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Self-test the fault-copilot MVP with a service manual."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and re-ingest the manual even if already in ChromaDB.",
    )
    parser.add_argument(
        "--skip-resolution",
        action="store_true",
        help="Skip the (slow/costly) Claude resolution tests.",
    )
    parser.add_argument(
        "--use-vision",
        action="store_true",
        help="Enable Claude Vision for image description during ingestion "
             "(slower, requires API key; default is text-only).",
    )
    parser.add_argument(
        "--manual",
        default=None,
        metavar="PATH",
        help="Override manual PDF path (e.g. manuals/8520-um511_-en-p.pdf).",
    )
    parser.add_argument(
        "--manual-id",
        default=None,
        metavar="ID",
        help="Override manual ID in ChromaDB (e.g. ab_9series_cnc_lathe).",
    )
    args = parser.parse_args()

    # Apply manual overrides — mutate module globals so every helper sees them.
    global MANUAL_FILENAME, MANUAL_ID
    if args.manual_id:
        MANUAL_ID = args.manual_id
    if args.manual:
        MANUAL_FILENAME = Path(args.manual).name

    use_vision = args.use_vision
    max_pages  = None if use_vision else DEFAULT_MAX_PAGES

    started_at = datetime.now(timezone.utc)

    print()
    hr("═")
    print(bold("  fault-copilot  ·  Service Manual Self-Test"))
    print(dim(
        f"  {MANUAL_FILENAME}  ·  {MANUAL_ID}  ·  "
        f"{'text+vision' if use_vision else 'text-only'}  ·  "
        f"{started_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ))
    hr("═")

    # ── 1. Environment ────────────────────────────────────────────────────────
    section_header(1, "Environment Check")
    env_r = check_environment(explicit_manual_path=args.manual)
    print(f"\n  {'PASS' if env_r['passed'] else 'FAIL'}: " +
          (TICK if env_r["passed"] else CROSS))

    manual_path = env_r.get("manual_path")
    if not env_r["passed"]:
        print(f"\n  {red('Environment check failed — aborting.')}")
        _finalize({}, {}, {}, {}, {}, started_at, overall=False)
        sys.exit(1)

    # ── 2. Ingestion ─────────────────────────────────────────────────────────
    section_header(2, f"Manual Ingestion  [{MANUAL_ID}]")
    ing_r = check_and_ingest(
        manual_path, force=args.force,
        use_vision=use_vision, max_pages=max_pages,
    )
    print(f"\n  Result: " + (SKIP if ing_r.get("skipped") else (TICK if ing_r["passed"] else CROSS)))

    if not ing_r["passed"] and not ing_r.get("skipped"):
        print(f"  {WARN} Ingestion failed — retrieval/resolution tests will likely fail too.")

    # ── 3. Retrieval ──────────────────────────────────────────────────────────
    section_header(3, f"Retrieval Tests ({len(RETRIEVAL_QUERIES)} queries)")
    ret_r = test_retrieval()

    # ── 4. Resolution ─────────────────────────────────────────────────────────
    section_header(4, f"Fault Resolution Tests ({len(RESOLUTION_QUERIES)} queries)")
    if args.skip_resolution:
        print(f"  {SKIP} Skipped (--skip-resolution)")
        res_r: dict = {"passed": True, "queries": [], "usable": 0,
                       "details": ["Skipped by flag"], "errors": []}
    else:
        res_r = test_resolution()

    # ── 5. Logging ────────────────────────────────────────────────────────────
    section_header(5, "Logging Test")
    log_r = test_logging()

    # ── 6. Report ─────────────────────────────────────────────────────────────
    section_header(6, "Saving Report")
    try:
        generate_report(env_r, ing_r, ret_r, res_r, log_r, started_at)
        print(f"  {TICK} Report saved → {REPORT_PATH}")
    except Exception as exc:
        print(f"  {CROSS} Report generation failed: {exc}")

    # ── Final verdict ─────────────────────────────────────────────────────────
    sections = [
        ("Environment",  env_r["passed"]),
        ("Ingestion",    ing_r["passed"] or ing_r.get("skipped", False)),
        ("Retrieval",    ret_r["passed"]),
        ("Resolution",   res_r["passed"]),
        ("Logging",      log_r["passed"]),
    ]
    overall = all(p for _, p in sections)

    print()
    hr("═")
    print(bold("  RESULTS SUMMARY"))
    hr()
    for name, passed in sections:
        icon = TICK if passed else CROSS
        print(f"  {icon}  {name}")
    hr("═")
    if overall:
        print(bold(green("  ✓ ALL TESTS PASSED")))
    else:
        print(bold(red("  ✗ SOME TESTS FAILED  (see report for details)")))
        failed = [n for n, p in sections if not p]
        print(f"  {WARN} Failed: {', '.join(failed)}")
    print(f"\n  Report: {REPORT_PATH}")
    hr("═")
    print()

    sys.exit(0 if overall else 1)


def _finalize(env_r, ing_r, ret_r, res_r, log_r, started_at, overall):
    try:
        generate_report(
            env_r or {"passed": False, "details": [], "errors": ["Aborted early"]},
            ing_r or {"passed": False, "skipped": False, "chunks": 0, "details": [], "errors": []},
            ret_r or {"passed": False, "queries": [], "zero_count": 0, "details": [], "errors": []},
            res_r or {"passed": False, "queries": [], "usable": 0, "details": [], "errors": []},
            log_r or {"passed": False, "fault_id": None, "details": [], "errors": []},
            started_at,
        )
        print(f"  Partial report saved → {REPORT_PATH}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
