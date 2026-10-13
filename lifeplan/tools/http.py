"""Shared HTTP helper for every external API the agents use.

* one `requests.Session` with a proper User-Agent (required by OpenStreetMap services)
* timeouts on every call
* one retry with backoff on 429 / 5xx / network errors
* a small in-memory TTL cache so repeated agent calls don't burn free-tier quotas
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Optional

import requests

from lifeplan.config import get_settings

log = logging.getLogger("lifeplan.http")


class ExternalAPIError(RuntimeError):
    """Raised when an external API fails after retries. Tools catch this and fall back."""

    def __init__(self, service: str, message: str, status: Optional[int] = None):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status = status


_session_lock = threading.Lock()
_session: Optional[requests.Session] = None
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_last_call: dict[str, float] = {}

# Minimum spacing between calls per host (public OSM services ask for <= 1 req/s)
_HOST_MIN_INTERVAL = {
    "nominatim.openstreetmap.org": 1.1,
    "overpass-api.de": 1.0,
}


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers["User-Agent"] = f"LifePlanAI/1.0 ({get_settings().osm_contact})"
            _session = s
        return _session


def set_session(s: requests.Session | None) -> None:
    """Used by tests to inject a fake session."""
    global _session
    with _session_lock:
        _session = s
    clear_cache()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_key(method: str, url: str, params: Any, body: Any) -> str:
    raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _throttle(url: str) -> None:
    host = url.split("/")[2] if "://" in url else url
    gap = _HOST_MIN_INTERVAL.get(host)
    if not gap:
        return
    now = time.monotonic()
    wait = _last_call.get(host, 0) + gap - now
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def request_json(
    service: str,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    data: Any = None,
    headers: dict | None = None,
    timeout: float | None = None,
    cache_ttl: float = 600,
    retries: int = 1,
) -> Any:
    """Perform an HTTP request and return parsed JSON, raising ExternalAPIError on failure."""
    key = _cache_key(method, url, params, json_body or data)
    if cache_ttl:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > time.time():
                return hit[1]

    timeout = timeout or get_settings().http_timeout
    last_error: str = "unknown error"
    status: Optional[int] = None
    for attempt in range(retries + 1):
        try:
            _throttle(url)
            resp = session().request(
                method, url, params=params, json=json_body, data=data, headers=headers, timeout=timeout
            )
            status = resp.status_code
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}"
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ExternalAPIError(service, last_error, status)
            if status >= 400:
                snippet = resp.text[:200].replace("\n", " ")
                raise ExternalAPIError(service, f"HTTP {status}: {snippet}", status)
            payload = resp.json()
            if cache_ttl:
                with _cache_lock:
                    _cache[key] = (time.time() + cache_ttl, payload)
            return payload
        except ExternalAPIError:
            raise
        except (requests.RequestException, ValueError) as exc:  # network error / bad JSON
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
    raise ExternalAPIError(service, last_error, status)
