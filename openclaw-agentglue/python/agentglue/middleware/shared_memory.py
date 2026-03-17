"""Shared memory store for cross-agent knowledge sharing.

OPTIONAL / SCAFFOLDED MODULE
----------------------------
This middleware is NOT part of the core v0.1 product surface.
It is disabled by default (shared_memory=False) and provided as an
optional extension for teams experimenting with cross-agent knowledge
sharing. The API may change without a major version bump.

Purpose
-------
When enabled, allows agents to publish discoveries and read each other's
findings, potentially reducing redundant tool calls in collaborative workflows.

Current status
--------------
- Write path is exercised in the runtime (tool results auto-published)
- Read path is NOT yet integrated; callers must explicitly read from memory
- Metrics track writes, but read/hit/miss metrics require explicit read() calls
- Consider this module "beta" until read path is better integrated

For v0.1, use exact-match dedup + TTL cache (the default) instead.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MemoryEntry:
    key: str
    value: Any
    agent_id: str
    created_at: float = field(default_factory=time.monotonic)
    ttl: float = 600.0  # default 10 minutes
    confidence: float = 1.0
    scope: str = "shared"  # private | shared | team

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.ttl

    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at


@dataclass
class SharedMemoryMetrics:
    """Metrics for shared memory operations.

    These are tracked internally by SharedMemory and can be accessed
    via the `metrics` property on a SharedMemory instance.
    """
    writes: int = 0
    reads: int = 0
    hits: int = 0
    misses: int = 0
    stale_reads: int = 0  # reads that found an expired entry
    private_access_denied: int = 0  # reads blocked by scope


class SharedMemory:
    """Thread-safe shared memory for multi-agent knowledge.

    OPTIONAL / SCAFFOLDED: Not part of core v0.1. Disabled by default.

    Features:
    - TTL-based expiration with staleness detection
    - Confidence scores (decay over time optional)
    - Scope control: private (single agent), shared (all agents), team (group)
    - Internal metrics: writes, reads, hits, misses, stale reads
    """

    def __init__(self, default_ttl: float = 600.0, min_confidence: float = 0.0):
        self.default_ttl = default_ttl
        self.min_confidence = min_confidence
        self._store: Dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()
        self._metrics = SharedMemoryMetrics()

    @property
    def metrics(self) -> SharedMemoryMetrics:
        """Return a snapshot of current metrics."""
        with self._lock:
            return SharedMemoryMetrics(
                writes=self._metrics.writes,
                reads=self._metrics.reads,
                hits=self._metrics.hits,
                misses=self._metrics.misses,
                stale_reads=self._metrics.stale_reads,
                private_access_denied=self._metrics.private_access_denied,
            )

    def write(
        self,
        key: str,
        value: Any,
        agent_id: str = "",
        ttl: float | None = None,
        confidence: float = 1.0,
        scope: str = "shared",
    ) -> None:
        entry = MemoryEntry(
            key=key,
            value=value,
            agent_id=agent_id,
            ttl=ttl or self.default_ttl,
            confidence=confidence,
            scope=scope,
        )
        with self._lock:
            self._store[key] = entry
            self._metrics.writes += 1

    def read(
        self,
        key: str,
        agent_id: str = "",
        min_confidence: float | None = None,
    ) -> Optional[Any]:
        threshold = min_confidence if min_confidence is not None else self.min_confidence
        with self._lock:
            self._metrics.reads += 1
            entry = self._store.get(key)
            if entry is None:
                self._metrics.misses += 1
                return None
            if entry.expired:
                del self._store[key]
                self._metrics.stale_reads += 1
                return None
            if entry.confidence < threshold:
                self._metrics.misses += 1
                return None
            if entry.scope == "private" and entry.agent_id != agent_id:
                self._metrics.private_access_denied += 1
                return None
            self._metrics.hits += 1
            return entry.value

    def read_entry(self, key: str, agent_id: str = "") -> Optional[MemoryEntry]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expired:
                del self._store[key]
                return None
            if entry.scope == "private" and entry.agent_id != agent_id:
                return None
            return entry

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def keys(self, agent_id: str = "", scope: str | None = None) -> List[str]:
        with self._lock:
            result = []
            for k, entry in self._store.items():
                if entry.expired:
                    continue
                if entry.scope == "private" and entry.agent_id != agent_id:
                    continue
                if scope and entry.scope != scope:
                    continue
                result.append(k)
            return result

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return sum(1 for e in self._store.values() if not e.expired)

    @property
    def hit_rate(self) -> float:
        """Fraction of reads that returned a valid (non-stale, accessible) value."""
        m = self.metrics
        if m.reads == 0:
            return 0.0
        return m.hits / m.reads

    def summary(self) -> Dict[str, Any]:
        """Return a summary of current state and metrics."""
        m = self.metrics
        return {
            "entries": self.size,
            "writes": m.writes,
            "reads": m.reads,
            "hits": m.hits,
            "misses": m.misses,
            "stale_reads": m.stale_reads,
            "private_access_denied": m.private_access_denied,
            "hit_rate": round(self.hit_rate, 3),
        }
