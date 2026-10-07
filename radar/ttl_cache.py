"""Tiny thread-safe in-memory TTL cache for provider calls."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(
        self, key: str, ttl_s: float, factory: Callable[[], Any], cache_empty: bool = True
    ) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < ttl_s:
                return hit[1]
        value = factory()
        if value or cache_empty:
            with self._lock:
                self._data[key] = (time.monotonic(), value)
        return value
