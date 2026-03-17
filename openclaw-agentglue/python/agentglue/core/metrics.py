"""Runtime metrics collection for AgentGlue.

Tracks the first useful set of coordination metrics for v0.1:
- observed tool calls
- underlying tool executions
- dedup/cache saves
- basic latency totals
- baseline counters for later middleware
"""

import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class GlueMetrics:
    """Aggregate metrics for an AgentGlue session."""

    tool_calls_total: int = 0
    tool_calls_underlying: int = 0
    tool_calls_deduped: int = 0
    tool_calls_coalesced: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latency_observed_ms: float = 0.0
    latency_underlying_ms: float = 0.0
    rate_limit_interventions: int = 0
    rate_limit_wait_time_ms: float = 0.0
    shared_memory_writes: int = 0
    shared_memory_reads: int = 0
    shared_memory_hits: int = 0
    shared_memory_misses: int = 0
    shared_memory_stale: int = 0
    task_conflicts_detected: int = 0
    task_conflicts_prevented: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_tool_call(
        self,
        *,
        deduped: bool = False,
        cache_hit: bool = False,
        latency_ms: float = 0.0,
        underlying_latency_ms: float = 0.0,
    ) -> None:
        with self._lock:
            self.tool_calls_total += 1
            self.latency_observed_ms += latency_ms
            if deduped:
                self.tool_calls_deduped += 1
            else:
                self.tool_calls_underlying += 1
                self.latency_underlying_ms += underlying_latency_ms or latency_ms

            if cache_hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def record_coalesced(self, count: int = 1) -> None:
        """Record calls that joined an in-flight execution (single-flight)."""
        with self._lock:
            self.tool_calls_coalesced += count

    def record_rate_limit(self, wait_ms: float = 0.0) -> None:
        with self._lock:
            self.rate_limit_interventions += 1
            self.rate_limit_wait_time_ms += wait_ms

    def record_memory_write(self) -> None:
        with self._lock:
            self.shared_memory_writes += 1

    def record_memory_access(self, hit: bool, stale: bool = False) -> None:
        with self._lock:
            self.shared_memory_reads += 1
            if hit and not stale:
                self.shared_memory_hits += 1
            elif stale:
                self.shared_memory_stale += 1
            else:
                self.shared_memory_misses += 1

    def record_conflict(self, prevented: bool = True) -> None:
        with self._lock:
            self.task_conflicts_detected += 1
            if prevented:
                self.task_conflicts_prevented += 1

    @property
    def dedup_rate(self) -> float:
        if self.tool_calls_total == 0:
            return 0.0
        return self.tool_calls_deduped / self.tool_calls_total

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total

    @property
    def calls_saved(self) -> int:
        return self.tool_calls_total - self.tool_calls_underlying

    @property
    def avg_observed_latency_ms(self) -> float:
        if self.tool_calls_total == 0:
            return 0.0
        return self.latency_observed_ms / self.tool_calls_total

    @property
    def avg_underlying_latency_ms(self) -> float:
        if self.tool_calls_underlying == 0:
            return 0.0
        return self.latency_underlying_ms / self.tool_calls_underlying

    def summary(self) -> Dict:
        return {
            "tool_calls_total": self.tool_calls_total,
            "tool_calls_underlying": self.tool_calls_underlying,
            "tool_calls_deduped": self.tool_calls_deduped,
            "tool_calls_coalesced": self.tool_calls_coalesced,
            "calls_saved": self.calls_saved,
            "dedup_rate": self.dedup_rate,
            "cache_hit_rate": self.cache_hit_rate,
            "avg_observed_latency_ms": round(self.avg_observed_latency_ms, 3),
            "avg_underlying_latency_ms": round(self.avg_underlying_latency_ms, 3),
            "rate_limit_interventions": self.rate_limit_interventions,
            "shared_memory_writes": self.shared_memory_writes,
            "shared_memory_hits": self.shared_memory_hits,
            "task_conflicts_prevented": self.task_conflicts_prevented,
        }

    def report(self) -> str:
        lines = [
            "AgentGlue Report:",
            f"  Observed tool calls:      {self.tool_calls_total}",
            f"  Underlying executions:    {self.tool_calls_underlying}",
            f"  Calls saved by dedup:     {self.calls_saved}/{self.tool_calls_total} ({self.dedup_rate:.0%})",
            f"  Coalesced (single-flight): {self.tool_calls_coalesced}",
            f"  Cache hit rate:           {self.cache_hit_rate:.0%}",
            f"  Avg observed latency:     {self.avg_observed_latency_ms:.2f} ms",
            f"  Avg underlying latency:   {self.avg_underlying_latency_ms:.2f} ms",
            f"  Rate limit interventions: {self.rate_limit_interventions}",
            f"  Shared memory writes:     {self.shared_memory_writes}",
            f"  Shared memory hits:       {self.shared_memory_hits}",
            f"  Task conflicts prevented: {self.task_conflicts_prevented}",
        ]
        return "\n".join(lines)
