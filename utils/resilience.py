"""Resilience primitives for external-service calls (UPGRADE v3.1 — hardening).

Three small, dependency-free building blocks shared by the enrichment/validation
clients (UMLS, Crossref, OpenAlex, Unpaywall):

  - ``CircuitBreaker``: after N consecutive failures it trips, short-circuiting
    further calls to a flapping service for the rest of the run instead of
    hammering it. Per-run, in-process (the worker runs all phases sequentially in
    one process, so the breaker state spans the whole run).
  - A per-run service-health registry so the report can honestly declare which
    services were degraded ("Unpaywall unavailable; coverage reduced").
  - ``JsonFileCache``: a tiny file-backed key/value cache so expensive lookups
    (CUI verification, retraction status) survive across runs.

Design rule (matches the project's "never fake rigor"): degrade loudly, never
silently. A degraded service is recorded and surfaced, not hidden.
"""

from __future__ import annotations

import json
from pathlib import Path


class CircuitBreaker:
    """Consecutive-failure circuit breaker. Not thread-safe by design — the
    pipeline worker is single-process/sequential."""

    def __init__(self, name: str, failure_threshold: int = 5):
        self.name = name
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_failures = 0
        self.tripped = False

    def allow(self) -> bool:
        """True if calls are still permitted (breaker not tripped)."""
        return not self.tripped

    def record_success(self) -> None:
        self.total_calls += 1
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.total_calls += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.tripped = True

    def status(self) -> dict:
        if self.tripped:
            state = "tripped"
        elif self.total_failures:
            state = "degraded"
        else:
            state = "ok"
        return {
            "state": state,
            "calls": self.total_calls,
            "failures": self.total_failures,
            "tripped": self.tripped,
        }


# Per-run registry of breakers, keyed by service name.
_BREAKERS: dict[str, CircuitBreaker] = {}


def breaker(name: str, failure_threshold: int = 5) -> CircuitBreaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name, failure_threshold)
    return _BREAKERS[name]


def health_report() -> dict:
    """Snapshot of every service's health for the manifest / QA sheet."""
    return {name: b.status() for name, b in _BREAKERS.items()}


def degraded_services() -> list[str]:
    """Names of services that tripped or saw failures this run."""
    return [name for name, b in _BREAKERS.items() if b.status()["state"] != "ok"]


def reset_all() -> None:
    """Clear breaker state (new run / tests)."""
    _BREAKERS.clear()


class JsonFileCache:
    """A minimal JSON-file-backed key/value cache. Loads on init, flushes on
    ``save()``. Values must be JSON-serialisable."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict = {}
        self._dirty = False
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            self._dirty = False
        except OSError:
            pass

    def __len__(self) -> int:
        return len(self._data)
