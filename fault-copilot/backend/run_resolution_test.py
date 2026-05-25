#!/usr/bin/env python3
"""
Limited resolution test — PM10010 Service Manual.
Exactly 5 Claude API calls, no vision, saves to logs/limited_resolution_test.md.

Run from fault-copilot/:
    python backend/run_resolution_test.py
"""
from __future__ import annotations

import logging as _log_stdlib  # noqa — cache stdlib before sys.path changes
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR  = Path(__file__).resolve().parent   # backend/
_PROJECT_DIR = _SCRIPT_DIR.parent               # fault-copilot/
_LOGGING_DIR = _SCRIPT_DIR / "fault_logging"

for _p in (_SCRIPT_DIR, _LOGGING_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from dotenv import load_dotenv as _ld
    _ld(_PROJECT_DIR / ".env", override=False)
except Exception:
    pass

import anthropic
from config import ANTHROPIC_API_KEY
from retrieval.retriever import retrieve_context
from resolution.resolver import resolve_fault
from fault_logger import log_fault_start, log_resolution_complete

# ── Config ────────────────────────────────────────────────────────────────────
MANUAL_ID   = "pm10010_service_manual"
REPORT_PATH = _PROJECT_DIR / "logs" / "limited_resolution_test.md"

QUERIES = [
    "The lamp is not on and the turntable is not rotating.",
    "The fuse is broken and transformer may be short circuited.",
    "How should I safely check the door interlock?",
    "There may be microwave leakage near the door seal.",
    "How do I discharge the capacitor safely before checking transformer resistance?",
]

# ── ANSI helpers ──────────────────────────────────────────────────────────────
_ANSI = sys.stdout.isatty()
def _c(code: str, t: str) -> str: return f"\033[{code}m{t}\033[0m" if _ANSI else t
def green(t):  return _c("92", t)
def red(t):    return _c("91", t)
def yellow(t): return _c("93", t)
def cyan(t):   return _c("96", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)

CONF_COLOR = {"high": green, "medium": yellow, "low": red, "none": red}
CONF_ICON  = {"high": "🟢",  "medium": "🟡",  "low": "🔴", "none": "⚫"}

def hr(w: int = 62) -> None: print("─" * w)
def hr2(w: int = 62) -> None: print("═" * w)


# ── Core test logic ───────────────────────────────────────────────────────────
def run_query(
    query: str,
    client: anthropic.Anthropic,
    idx: int,
    total: int,
) -> dict:
    print(f"\n{'─'*62}")
    print(bold(f"  [{idx}/{total}]  {query}"))
    print()

    # Retrieve
    context = retrieve_context(query, [MANUAL_ID], n_results=5)
    if context:
        top = context[0]
        print(f"  {green('✓')} {len(context)} chunk(s) — "
              f"top: p.{top.get('page_num','?')} · "
              f"{(top.get('section_heading') or 'no heading')[:55]}")
        snippet = (top.get("content") or "")[:200].replace("\n", " ")
        print(f"  {dim(snippet + '…')}")
    else:
        print(f"  {yellow('⚠')} No chunks retrieved")

    # Resolve — one Claude call
    resolution = resolve_fault(query, [MANUAL_ID], "operator", client)
    conf    = resolution.get("confidence", "none")
    steps   = resolution.get("steps", [])
    refs    = resolution.get("manual_references", [])
    message = resolution.get("message", "")

    cf_str = CONF_COLOR.get(conf, red)(conf.upper())
    print(f"\n  Confidence : {cf_str}")
    print(f"  Steps      : {len(steps)}")

    if steps:
        print()
        for step in steps[:5]:
            safety_tag = f"  {red('[SAFETY]')}"   if step.get("safety_critical") else ""
            esc_tag    = f"  {yellow('[ESCALATE]')}" if step.get("escalate")       else ""
            text = step["instruction"]
            # Wrap long instructions at ~100 chars
            if len(text) > 100:
                text = text[:100] + "…"
            print(f"    Step {step['step_num']:>2}: {text}{safety_tag}{esc_tag}")
    elif message:
        print(f"\n  {yellow('⚠')} {message}")

    if refs:
        print(f"\n  Refs       : {', '.join(refs[:4])}")

    # Log to SQLite
    fault_id = None
    try:
        fault_id = log_fault_start(
            fault_input=query,
            manual_ids=[MANUAL_ID],
            operator_level="operator",
            confidence=conf,
            steps_generated=len(steps),
            manual_references=refs,
            plant_id=None,
        )
        log_resolution_complete(
            fault_id=fault_id,
            accepted=bool(steps),
            time_seconds=0,
        )
        print(f"\n  {green('✓')} Logged → fault_id={fault_id}")
    except Exception as exc:
        print(f"\n  {yellow('⚠')} Logging failed: {exc}", file=sys.stderr)

    return {
        "query":          query,
        "context_chunks": len(context),
        "confidence":     conf,
        "steps":          steps,
        "refs":           refs,
        "message":        message,
        "fault_id":       fault_id,
    }


# ── Report ────────────────────────────────────────────────────────────────────
def save_report(results: list[dict], started: datetime) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    usable  = sum(1 for r in results if r["steps"])
    verdict = "✅ PASS" if usable == len(results) else f"⚠️ {usable}/{len(results)} queries produced steps"

    lines: list[str] = [
        "# Limited Resolution Test — PM10010 Microwave Oven Service Manual",
        "",
        f"**Date:** {started.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Manual ID:** `{MANUAL_ID}`  ",
        f"**Mode:** text-only · no vision · {len(results)} Claude calls  ",
        "",
        f"## Verdict: {verdict}",
        "",
        "| # | Query | Conf | Steps | fault_id |",
        "|---|-------|------|-------|----------|",
    ]
    for i, r in enumerate(results, 1):
        ci = CONF_ICON.get(r["confidence"], "⚫")
        lines.append(
            f"| {i} | {r['query'][:60]} | {ci} {r['confidence']} "
            f"| {len(r['steps'])} | {r['fault_id']} |"
        )
    lines.append("")

    for i, r in enumerate(results, 1):
        ci = CONF_ICON.get(r["confidence"], "⚫")
        lines += [
            "---",
            f"### Query {i}: _{r['query']}_",
            "",
            f"- **Confidence:** {ci} **{r['confidence'].upper()}**",
            f"- **Context chunks retrieved:** {r['context_chunks']}",
            f"- **Steps generated:** {len(r['steps'])}",
            f"- **fault_id (SQLite):** {r['fault_id']}",
        ]
        if r["refs"]:
            lines.append(f"- **Manual sections:** {', '.join(r['refs'])}")
        lines.append("")

        if r["steps"]:
            lines.append("**Steps (first 5):**")
            lines.append("")
            for step in r["steps"][:5]:
                tags = ""
                if step.get("safety_critical"): tags += " ⚠ SAFETY"
                if step.get("escalate"):        tags += " 📞 ESCALATE"
                lines.append(f"{step['step_num']}. {step['instruction']}{tags}")
            lines.append("")
        elif r["message"]:
            lines.append(f"> ⚠ {r['message']}")
            lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    if not ANTHROPIC_API_KEY:
        print(red("✗ ANTHROPIC_API_KEY not set — add it to .env"), file=sys.stderr)
        sys.exit(1)

    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    started = datetime.now(timezone.utc)

    print()
    hr2()
    print(bold("  PM10010 Service Manual · Limited Resolution Test"))
    print(dim(
        f"  {MANUAL_ID}  ·  text-only  ·  "
        f"{len(QUERIES)} Claude calls  ·  "
        f"{started.strftime('%Y-%m-%d %H:%M UTC')}"
    ))
    hr2()

    results = [run_query(q, client, i, len(QUERIES)) for i, q in enumerate(QUERIES, 1)]

    # Summary
    print()
    hr2()
    print(bold("  RESULTS SUMMARY"))
    hr()
    usable   = sum(1 for r in results if r["steps"])
    all_pass = usable == len(QUERIES)
    for r in results:
        icon = green("✓") if r["steps"] else red("✗")
        cf   = CONF_COLOR.get(r["confidence"], red)(r["confidence"])
        print(f"  {icon}  [{cf:>6}]  {len(r['steps'])} steps  "
              f"{r['query'][:55]}…" if len(r["query"]) > 55 else
              f"  {icon}  [{cf:>6}]  {len(r['steps'])} steps  {r['query']}")
    hr2()
    verdict_str = green("✓ ALL 5 QUERIES PASSED") if all_pass else yellow(f"⚠ {usable}/5 queries produced steps")
    print(bold(f"  {verdict_str}"))

    save_report(results, started)
    print(f"\n  Report → {REPORT_PATH}")
    hr2()
    print()

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
