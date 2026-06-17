"""Simple in-memory rate limiting for public API endpoints."""

import os
import time
from collections import defaultdict, deque
from threading import Lock

_lock = Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)

DEFAULT_LIMIT = int(os.getenv("QUERY_RATE_LIMIT", "30"))
DEFAULT_WINDOW_SECONDS = int(os.getenv("QUERY_RATE_WINDOW_SECONDS", "60"))


def _client_key(*parts: str) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "|".join(cleaned) if cleaned else "anonymous"


def is_rate_limited(
    *key_parts: str,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> tuple[bool, int]:
    """
    Return (limited, retry_after_seconds).

    Uses a sliding window per key. Suitable for single-process dev and
    one-worker Gunicorn; use Redis for multi-instance production.
    """
    max_requests = limit if limit is not None else DEFAULT_LIMIT
    window = window_seconds if window_seconds is not None else DEFAULT_WINDOW_SECONDS
    key = _client_key(*key_parts)
    now = time.monotonic()
    cutoff = now - window

    with _lock:
        bucket = _hits[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            retry_after = max(1, int(window - (now - bucket[0])))
            return True, retry_after
        bucket.append(now)
    return False, 0


def reset_rate_limits() -> None:
    """Clear counters (useful in tests)."""
    with _lock:
        _hits.clear()
