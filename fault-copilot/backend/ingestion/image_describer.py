"""Describe diagrams and images using Claude Vision."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import anthropic
from anthropic import Anthropic

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config import VISION_MODEL  # noqa: E402


SYSTEM_PROMPT = (
    "You are analyzing a technical diagram from an industrial equipment "
    "maintenance manual. Describe what this diagram shows in precise "
    "technical terms that would help a maintenance technician understand "
    "what components are shown, their relationships, and any labels or "
    "measurements visible. Be specific and complete."
)

_MAX_RETRIES = 3
_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


def describe_image(
    base64_image: str,
    section_context: str,
    client: Anthropic,
) -> str:
    """Return a Claude Vision description of the given base64 PNG image.

    Retries transient errors (rate limits, connection failures, 5xx) with
    exponential backoff up to _MAX_RETRIES additional attempts. Returns an
    empty string if every attempt fails.
    """
    user_text = (
        f"Section: {section_context}\n\nDescribe this technical diagram:"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_image,
                    },
                },
                {"type": "text", "text": user_text},
            ],
        }
    ]

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=VISION_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            return "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
        except _RETRYABLE as exc:
            last_error = exc
            if attempt >= _MAX_RETRIES:
                break
            delay = 2 ** attempt
            print(
                f"  [warn] vision attempt {attempt + 1} failed ({type(exc).__name__}); "
                f"retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
        except anthropic.APIStatusError as exc:
            # Non-retryable API error (e.g. 400 bad request)
            print(
                f"  [error] vision call failed with non-retryable error: {exc}",
                file=sys.stderr,
            )
            return ""

    print(
        f"  [error] vision call failed after {_MAX_RETRIES + 1} attempts: {last_error}",
        file=sys.stderr,
    )
    return ""
