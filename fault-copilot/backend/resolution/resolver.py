"""Generate step-by-step fault resolution guidance with Claude."""

from __future__ import annotations

import re
import sys
import time as _time
from pathlib import Path
from typing import Optional

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from anthropic import Anthropic  # noqa: E402

from config import (  # noqa: E402
    CONFIDENCE_HIGH_THRESHOLD,
    CONFIDENCE_MEDIUM_THRESHOLD,
    RESOLVER_CACHE_SIZE,
    RESOLVER_CACHE_TTL,
    RESOLVER_MODEL,
)
from retrieval.retriever import retrieve_context  # noqa: E402


# ---------------------------------------------------------------------------
# In-process resolution cache (1C)
# Dependency-free TTL + LRU implementation — no cachetools required.
# ---------------------------------------------------------------------------

class _TTLCache:
    """Minimal TTL-aware LRU dict.  Not thread-safe; fine for single-process use."""

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl     = ttl
        self._store:  dict = {}   # key -> value
        self._stamps: dict = {}   # key -> insertion monotonic timestamp

    def get(self, key) -> Optional[dict]:
        if key not in self._store:
            return None
        if self._ttl > 0 and (_time.monotonic() - self._stamps[key]) > self._ttl:
            del self._store[key]
            del self._stamps[key]
            return None
        return self._store[key]

    def set(self, key, value) -> None:
        if self._maxsize <= 0:
            return
        if len(self._store) >= self._maxsize and key not in self._store:
            # Evict the entry with the oldest insertion timestamp
            oldest = min(self._stamps, key=self._stamps.__getitem__)
            del self._store[oldest]
            del self._stamps[oldest]
        self._store[key]  = value
        self._stamps[key] = _time.monotonic()


_RESOLVER_CACHE: Optional[_TTLCache] = (
    _TTLCache(maxsize=RESOLVER_CACHE_SIZE, ttl=RESOLVER_CACHE_TTL)
    if RESOLVER_CACHE_SIZE > 0 else None
)


# Matches step markers in all formats Claude commonly produces:
#   "Step 1: "         plain
#   "**Step 1 — "      markdown bold + em dash  (most common with Sonnet 4.x)
#   "**Step 1 - "      markdown bold + hyphen
#   "1. " / "1: "      bare number
_STEP_MARKER_RE = re.compile(
    r"^\*{0,2}(?:Step\s+)?(\d+)\*{0,2}\s*[—–\-.:]",
    re.MULTILINE | re.IGNORECASE,
)

# Strips residual markdown so instructions are plain text
_MD_RE = re.compile(r"\*{1,3}|^#+\s*", re.MULTILINE)

_SAFETY_KEYWORDS = frozenset(
    [
        "safety",
        "loto",
        "lockout",
        "tagout",
        "warning",
        "caution",
        "danger",
        "stop the line",
        "emergency stop",
        "de-energize",
        "power off",
        "shut down",
    ]
)

_NO_RESULTS_RESPONSE = {
    "steps": [],
    "confidence": "none",
    "message": (
        "No relevant procedure found. "
        "Please consult your senior technician."
    ),
    "retrieved_context": [],
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_context_block(chunks: list[dict]) -> str:
    lines: list[str] = []
    for chunk in chunks:
        heading = chunk.get("section_heading") or "Unknown Section"
        page = chunk.get("page_num", "?")
        content = chunk.get("content", "")
        lines.append(f"[Section: {heading} | Page: {page}]")
        lines.append(content)
        lines.append("---")
    return "\n".join(lines)


def _build_system_prompt(operator_level: str) -> str:
    return (
        f"You are a fault resolution assistant for industrial packaging equipment. "
        f"Your job is to help {operator_level}s resolve equipment faults safely and "
        f"correctly by referencing the provided manual sections.\n\n"
        "Rules you must always follow:\n"
        "- Always start with a SAFETY step (LOTO if required, stop the line, "
        "confirm safe state)\n"
        "- Number every step clearly (Step 1, Step 2, etc.)\n"
        "- Use simple, direct language — no jargon unless it matches the manual exactly\n"
        "- If a step requires a tool, name it specifically\n"
        "- If a step has a measurement or spec value, include the exact number from "
        "the manual\n"
        "- If the procedure is unclear from the context, say "
        '"ESCALATE: Contact senior technician for this step" rather than guessing\n'
        "- End with a VERIFY step (how to confirm the fault is resolved before "
        "restarting)\n"
        "- Maximum 10 steps. If more are needed, split into Phase 1 and Phase 2."
    )


def _build_user_message(
    fault_input: str, context_block: str, operator_level: str
) -> str:
    return (
        f"FAULT REPORTED: {fault_input}\n\n"
        f"RELEVANT MANUAL SECTIONS:\n"
        f"{context_block}\n\n"
        f"Generate step-by-step resolution procedure for a {operator_level}. "
        "Reference specific manual sections where relevant."
    )


def _parse_steps(raw: str) -> list[dict]:
    """Extract numbered steps from Claude's plain-text response.

    Uses line-anchored markers so numbers embedded in measurements
    (e.g. "185°C", "E47") are not mistaken for step numbers.
    """
    markers = list(_STEP_MARKER_RE.finditer(raw))
    if not markers:
        return []

    steps: list[dict] = []
    for idx, match in enumerate(markers):
        step_num = int(match.group(1))
        body_start = match.end()
        body_end = markers[idx + 1].start() if idx + 1 < len(markers) else len(raw)
        body = raw[body_start:body_end].strip()

        # Strip markdown formatting then collapse to one readable block.
        clean = _MD_RE.sub("", body)
        instruction = " ".join(clean.split())
        if not instruction:
            continue

        lower = instruction.lower()
        safety_critical = step_num == 1 or any(kw in lower for kw in _SAFETY_KEYWORDS)
        escalate = "escalate:" in lower

        steps.append(
            {
                "step_num": step_num,
                "instruction": instruction,
                "safety_critical": safety_critical,
                "escalate": escalate,
            }
        )
    return steps


def _compute_confidence(retrieved: list[dict]) -> str:
    """Determine confidence tier from retrieval results.

    Tiers (score-based, not fault-code-dependent)
    -----
    none   — no chunks retrieved
    low    — best relevance_score < CONFIDENCE_MEDIUM_THRESHOLD
    medium — at least one score >= CONFIDENCE_MEDIUM_THRESHOLD, or fewer than 3 strong
    high   — 3+ chunks with relevance_score >= CONFIDENCE_HIGH_THRESHOLD

    Bonus: if any chunk has match_type == "exact" (literal fault-code match),
    the tier is bumped one level up to reward structured fault-code queries.
    """
    if not retrieved:
        return "none"

    scores    = [r.get("relevance_score", 0.0) for r in retrieved]
    best      = max(scores, default=0.0)
    strong    = sum(1 for s in scores if s >= CONFIDENCE_HIGH_THRESHOLD)
    has_exact = any(r.get("match_type") == "exact" for r in retrieved)

    if best < CONFIDENCE_MEDIUM_THRESHOLD:
        tier = "low"
    elif strong >= 3:
        tier = "high"
    else:
        tier = "medium"

    # Exact fault-code match bumps one tier — preserves reward for code-based queries.
    if has_exact:
        tier = {"low": "medium", "medium": "high", "high": "high"}[tier]

    return tier


def _extract_manual_references(retrieved: list[dict], raw_response: str) -> list[str]:
    """Return unique section headings from retrieved chunks that appear in
    the response, preserving order of first occurrence."""
    raw_lower = raw_response.lower()
    seen: set[str] = set()
    refs: list[str] = []
    for chunk in retrieved:
        heading = chunk.get("section_heading") or ""
        if not heading or heading in seen:
            continue
        if heading.lower() in raw_lower:
            refs.append(heading)
        seen.add(heading)
    # Fall back to all retrieved headings if Claude paraphrased them
    if not refs:
        for chunk in retrieved:
            heading = chunk.get("section_heading") or ""
            if heading and heading not in seen:
                refs.append(heading)
                seen.add(heading)
    return refs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_fault(
    fault_input: str,
    manual_ids: list[str],
    operator_level: str,
    client: Anthropic,
) -> dict:
    """Retrieve context and generate structured fault resolution guidance.

    Parameters
    ----------
    fault_input:    Fault code or description from the operator.
    manual_ids:     Which ingested manuals to search.
    operator_level: "operator" or "technician" — adjusts language in the prompt.
    client:         Authenticated Anthropic client.

    Returns
    -------
    dict with keys: steps, confidence, manual_references, raw_response,
    retrieved_context (and optionally message on failure).
    """
    retrieved = retrieve_context(fault_input, manual_ids, n_results=5)

    if not retrieved:
        return {**_NO_RESULTS_RESPONSE}

    # ── Resolution cache lookup (1C) ──────────────────────────────────────────
    # Cache key covers the query + which manuals + operator vocabulary level.
    # The retrieved context is NOT part of the key — same query always hits the
    # same ChromaDB chunks, so the key is sufficient.
    _cache_key = (fault_input.strip().lower(), tuple(sorted(manual_ids)), operator_level)
    if _RESOLVER_CACHE is not None:
        cached = _RESOLVER_CACHE.get(_cache_key)
        if cached is not None:
            return dict(cached)  # return a shallow copy; caller may mutate it

    context_block = _build_context_block(retrieved)
    system_prompt = _build_system_prompt(operator_level)

    # ── Prompt caching via Anthropic cache_control (1D) ───────────────────────
    # System prompt + context block are marked ephemeral so repeated calls for
    # the same manual section can reuse cached KV state, reducing TTFT and
    # input-token billing. Silently ignored if tokens are below the 1024-token
    # minimum; no degradation on cache miss.
    try:
        response = client.messages.create(
            model=RESOLVER_MODEL,
            max_tokens=1000,
            temperature=0,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"RELEVANT MANUAL SECTIONS:\n{context_block}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"FAULT REPORTED: {fault_input}\n\n"
                            f"Generate step-by-step resolution procedure for a "
                            f"{operator_level}. Reference specific manual sections "
                            "where relevant."
                        ),
                    },
                ],
            }],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] Claude API call failed: {exc}", file=sys.stderr)
        return {
            "steps": [],
            "confidence": "none",
            "message": f"Resolution generation failed: {exc}",
            "retrieved_context": retrieved,
        }

    raw_response = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    steps = _parse_steps(raw_response)
    confidence = _compute_confidence(retrieved)
    manual_references = _extract_manual_references(retrieved, raw_response)

    result = {
        "steps": steps,
        "confidence": confidence,
        "manual_references": manual_references,
        "raw_response": raw_response,
        "retrieved_context": retrieved,
    }

    # ── Cache the result (1C) ─────────────────────────────────────────────────
    if _RESOLVER_CACHE is not None:
        _RESOLVER_CACHE.set(_cache_key, result)

    return result


def format_for_display(resolution: dict, language: str = "en") -> str:
    """Render a resolution dict as clean plain text for terminal or API output."""
    lines: list[str] = []

    if language == "hi":
        lines.append("हिंदी में जल्द उपलब्ध  (Hindi support coming soon)")
        lines.append("")

    confidence = resolution.get("confidence", "none").upper()
    message = resolution.get("message", "")
    steps: list[dict] = resolution.get("steps", [])
    references: list[str] = resolution.get("manual_references", [])
    retrieved: list[dict] = resolution.get("retrieved_context", [])

    lines.append("=" * 60)
    lines.append("FAULT RESOLUTION REPORT")
    lines.append("=" * 60)
    lines.append(f"Confidence : {confidence}")
    lines.append(f"Context    : {len(retrieved)} chunk(s) retrieved")

    if references:
        lines.append(f"Sections   : {', '.join(references)}")

    lines.append("")

    if message:
        lines.append(f"NOTE: {message}")
        lines.append("")

    if steps:
        lines.append("PROCEDURE")
        lines.append("-" * 40)
        for step in steps:
            prefix = "[!] " if step.get("safety_critical") else "    "
            lines.append(f"{prefix}Step {step['step_num']}: {step['instruction']}")
        lines.append("")

    if not steps and not message:
        raw = resolution.get("raw_response", "")
        if raw:
            lines.append(raw)

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()
    client = anthropic.Anthropic()
    result = resolve_fault(
        "sealing jaw temperature deviation E47",
        ["tba19_om"],
        "operator",
        client,
    )
    print(format_for_display(result))
