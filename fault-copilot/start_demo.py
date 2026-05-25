#!/usr/bin/env python3
"""
fault-copilot demo launcher.

Usage (from fault-copilot/ directory):
    python start_demo.py

What it does:
  1. Verifies .env and ANTHROPIC_API_KEY
  2. Ingests the service manual (text-only, fast) if not already done
  3. Starts the FastAPI backend on localhost:8000
  4. Starts a frontend HTTP server on localhost:3000
  5. Opens the browser automatically
"""
from __future__ import annotations

import sys as _sys_early
if hasattr(_sys_early.stdout, "reconfigure"):
    _sys_early.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(_sys_early.stderr, "reconfigure"):
    _sys_early.stderr.reconfigure(encoding="utf-8", errors="replace")

import functools
import http.server
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT        = Path(__file__).resolve().parent
_BACKEND     = _ROOT / "backend"
_FRONTEND    = _ROOT / "frontend"
_MANUALS     = _ROOT / "manuals"
_LOGS        = _ROOT / "logs"

DEFAULT_MANUAL   = _MANUALS / "service_manual_PM10010.pdf"
DEFAULT_MANUAL_ID = "pm10010_service_manual"
BACKEND_PORT  = 8000
FRONTEND_PORT = 3000

# ── ANSI colours ──────────────────────────────────────────────────────────────
if sys.stdout.isatty() and os.name != "nt":
    G, R, Y, B, BOLD, DIM, RST = "\033[92m","\033[91m","\033[93m","\033[96m","\033[1m","\033[2m","\033[0m"
else:
    # Windows PowerShell — enable VT processing, or fall back gracefully
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
        G, R, Y, B, BOLD, DIM, RST = "\033[92m","\033[91m","\033[93m","\033[96m","\033[1m","\033[2m","\033[0m"
    except Exception:
        G = R = Y = B = BOLD = DIM = RST = ""

def ok(msg):  print(f"  {G}✓{RST} {msg}")
def err(msg): print(f"  {R}✗{RST} {msg}")
def warn(msg):print(f"  {Y}⚠{RST} {msg}")
def info(msg):print(f"  {B}→{RST} {msg}")
def hr():     print("-" * 60)

# ── sys.path for local imports ────────────────────────────────────────────────
for _p in (_BACKEND, _BACKEND / "fault_logging"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── Process handles (for cleanup) ────────────────────────────────────────────
_backend_proc: subprocess.Popen | None = None
_frontend_server: http.server.HTTPServer | None = None


def _cleanup(sig=None, frame=None):
    print(f"\n{DIM}Shutting down…{RST}")
    if _backend_proc:
        _backend_proc.terminate()
    if _frontend_server:
        _frontend_server.shutdown()
    sys.exit(0)


signal.signal(signal.SIGINT,  _cleanup)
signal.signal(signal.SIGTERM, _cleanup)


# ── Step helpers ──────────────────────────────────────────────────────────────
def check_env() -> str:
    """Return the API key or exit with a clear message."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        err(f".env not found at {env_path}")
        info(f"Copy .env.example → .env and add your ANTHROPIC_API_KEY")
        sys.exit(1)
    ok(".env found")

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        warn("python-dotenv not installed; reading env directly")

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        err("ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)
    masked = key[:8] + "…" + key[-4:]
    ok(f"ANTHROPIC_API_KEY set  ({masked})")
    return key


def ensure_manual_ingested(api_key: str) -> str:
    """Ingest the default manual (text-only) if not already in ChromaDB."""
    # Import here so .env is already loaded
    from retrieval.retriever import manual_exists

    if manual_exists(DEFAULT_MANUAL_ID):
        ok(f"Manual already ingested  ({DEFAULT_MANUAL_ID})")
        return DEFAULT_MANUAL_ID

    if not DEFAULT_MANUAL.exists():
        err(f"Manual not found: {DEFAULT_MANUAL}")
        info("Place service_manual_PM10010.pdf in the manuals/ directory")
        sys.exit(1)

    warn(f"Manual not ingested — running text-only ingestion now…")
    info(f"File: {DEFAULT_MANUAL.name}  ({DEFAULT_MANUAL.stat().st_size // 1024} KB)")
    info("This takes ~15 seconds (text-only, no API calls)")
    print()

    from ingestion.embedder import ingest_manual
    try:
        chunks = ingest_manual(
            str(DEFAULT_MANUAL), DEFAULT_MANUAL_ID,
            client=None, use_vision=False, max_pages=120,
        )
    except Exception as exc:
        err(f"Ingestion failed: {exc}")
        sys.exit(1)

    if chunks == 0:
        err("Ingestion returned 0 chunks — PDF may be unreadable")
        sys.exit(1)

    ok(f"Ingested {chunks} chunks from {DEFAULT_MANUAL.name}")
    return DEFAULT_MANUAL_ID


def start_backend() -> subprocess.Popen:
    global _backend_proc
    _LOGS.mkdir(parents=True, exist_ok=True)
    log_file = open(_LOGS / "backend.log", "w")

    # Uvicorn runs from backend/ so relative .env paths (./data/chroma etc.)
    # would resolve incorrectly.  Convert them to absolute paths here.
    env = os.environ.copy()
    for var, default in [
        ("CHROMA_PATH",  _ROOT / "data" / "chroma"),
        ("LOG_DB_PATH",  _ROOT / "logs" / "faults.db"),
        ("MANUALS_PATH", _ROOT / "manuals"),
    ]:
        raw = env.get(var, "")
        if not raw or not Path(raw).is_absolute():
            env[var] = str(default)

    _backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT),
         "--log-level", "error"],
        cwd=str(_BACKEND),
        stdout=log_file,
        stderr=log_file,
        env=env,
    )
    return _backend_proc


def wait_for_backend(timeout: int = 30) -> bool:
    url = f"http://127.0.0.1:{BACKEND_PORT}/health"
    for i in range(timeout):
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            if i % 5 == 0 and i > 0:
                info(f"Waiting for backend… ({i}s)")
            time.sleep(1)
    return False


def start_frontend() -> http.server.HTTPServer:
    global _frontend_server

    class _SilentHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def log_error(self, *args): pass

    handler = functools.partial(_SilentHandler, directory=str(_FRONTEND))
    _frontend_server = http.server.HTTPServer(("127.0.0.1", FRONTEND_PORT), handler)
    thread = threading.Thread(target=_frontend_server.serve_forever, daemon=True)
    thread.start()
    return _frontend_server


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="fault-copilot demo launcher")
    parser.add_argument("--no-browser", action="store_true",
                        help="Skip opening the browser (useful for headless / CI testing)")
    args = parser.parse_args()

    print()
    print(f"{BOLD}{'=' * 60}{RST}")
    print(f"{BOLD}  fault-copilot  .  Demo Launcher{RST}")
    print("=" * 60)
    print()

    # 1. Environment
    print(f"{BOLD}[1/5] Environment{RST}")
    api_key = check_env()
    print()

    # 2. Manual
    print(f"{BOLD}[2/5] Manual{RST}")
    manual_id = ensure_manual_ingested(api_key)
    print()

    # 3. Backend
    print(f"{BOLD}[3/5] Backend  (port {BACKEND_PORT}){RST}")
    start_backend()
    info("Starting uvicorn...")
    if not wait_for_backend():
        err("Backend did not start within 30 seconds")
        info("Check logs/backend.log for details")
        sys.exit(1)
    ok(f"Backend ready  ->  http://localhost:{BACKEND_PORT}")
    print()

    # 4. Frontend
    print(f"{BOLD}[4/5] Frontend  (port {FRONTEND_PORT}){RST}")
    start_frontend()
    ok(f"Frontend ready  ->  http://localhost:{FRONTEND_PORT}")
    print()

    # 5. Browser
    frontend_url = f"http://localhost:{FRONTEND_PORT}"
    if args.no_browser:
        print(f"{BOLD}[5/5] Browser{RST}")
        info(f"--no-browser: skipped  ({frontend_url})")
    else:
        print(f"{BOLD}[5/5] Opening browser{RST}")
        webbrowser.open(frontend_url)
        ok(f"Browser opened  ->  {frontend_url}")
    print()

    # Status summary
    print("=" * 60)
    print(f"{BOLD}  System ready{RST}")
    hr()
    print(f"  Frontend  : {B}http://localhost:{FRONTEND_PORT}{RST}")
    print(f"  Backend   : {B}http://localhost:{BACKEND_PORT}{RST}")
    print(f"  API docs  : {B}http://localhost:{BACKEND_PORT}/docs{RST}")
    print(f"  Manual    : {manual_id}")
    print(f"  Log file  : logs/backend.log")
    hr()
    print(f"  {DIM}Press Ctrl+C to stop{RST}")
    print("=" * 60)
    print()

    # Keep running; restart backend if it crashes
    try:
        while True:
            if _backend_proc and _backend_proc.poll() is not None:
                warn("Backend process exited -- restarting...")
                start_backend()
                time.sleep(2)
            time.sleep(5)
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
