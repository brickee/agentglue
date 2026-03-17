"""AgentGlue main runtime — ties the v0.1 middleware together."""

import functools
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict

from agentglue.core.allocator import RateLimiter
from agentglue.core.metrics import GlueMetrics
from agentglue.core.recorder import EventRecorder
from agentglue.middleware.dedup import ToolDedup
from agentglue.middleware.shared_memory import SharedMemory
from agentglue.middleware.task_lock import TaskLock


class AgentGlue:
    """Main entry point for AgentGlue middleware.

    Usage:
        glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False)

        @glue.tool()
        def search(query: str) -> str:
            return call_api(query)

        print(glue.report())
    """

    def __init__(
        self,
        dedup: bool = True,
        dedup_ttl: float = 300.0,
        backend: str = "memory",
        db_path: str | None = None,
        shared_memory: bool = False,
        memory_ttl: float = 600.0,
        rate_limiter: bool = False,
        rate_limits: Dict[str, float] | None = None,
        task_lock: bool = False,
        record_events: bool = True,
    ):
        self.metrics = GlueMetrics()
        self.recorder = EventRecorder() if record_events else None

        self.dedup_enabled = dedup
        self.dedup = ToolDedup(
            default_ttl=dedup_ttl, backend=backend, db_path=db_path
        ) if dedup else None

        self.memory_enabled = shared_memory
        self.memory = SharedMemory(default_ttl=memory_ttl) if shared_memory else None

        self.rate_limiter_enabled = rate_limiter
        self.rate_limiter = RateLimiter(tool_rate_limits=rate_limits) if rate_limiter else None

        self.task_lock_enabled = task_lock
        self.task_lock = TaskLock() if task_lock else None

    def tool(
        self,
        name: str | None = None,
        ttl: float | None = None,
        rate_limit: float | None = None,
    ) -> Callable:
        """Decorator to wrap a tool function with AgentGlue middleware.

        Args:
            name: Override tool name (defaults to function name).
            ttl: Cache TTL in seconds for dedup.
            rate_limit: Max calls per second (creates rate limiter if enabled).
        """

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__

            if rate_limit is not None and self.rate_limiter:
                self.rate_limiter.add_tool(tool_name, rate_limit)

            @functools.wraps(func)
            def wrapper(*args, agent_id: str = "", **kwargs) -> Any:
                args_hash = self._hash_call(tool_name, args, kwargs)
                started = time.monotonic()

                # 1. Cache hit — return immediately
                if self.dedup_enabled and self.dedup:
                    entry = self.dedup.lookup(tool_name, args, kwargs)
                    if entry is not None:
                        observed_ms = (time.monotonic() - started) * 1000.0
                        self.metrics.record_tool_call(
                            deduped=True,
                            cache_hit=True,
                            latency_ms=observed_ms,
                        )
                        self._record_event(
                            "tool_call_deduped",
                            agent_id,
                            tool_name,
                            {
                                "args_hash": args_hash,
                                "original_agent": entry.agent_id,
                                "cache_age_s": round(entry.age, 6),
                            },
                        )
                        return entry.result

                    # 2. Single-flight: join an in-flight execution if one exists
                    cache_key = self.dedup._make_key(tool_name, args, kwargs)
                    flight = self.dedup.try_join_flight(cache_key)
                    if flight is not None:
                        self._record_event(
                            "tool_call_coalesced",
                            agent_id,
                            tool_name,
                            {"args_hash": args_hash, "action": "waiting"},
                        )
                        flight.event.wait()
                        if flight.error is not None:
                            raise flight.error
                        observed_ms = (time.monotonic() - started) * 1000.0
                        self.metrics.record_tool_call(
                            deduped=True,
                            cache_hit=True,
                            latency_ms=observed_ms,
                        )
                        self.metrics.record_coalesced()
                        self._record_event(
                            "tool_call_coalesced",
                            agent_id,
                            tool_name,
                            {"args_hash": args_hash, "action": "resolved", "latency_ms": round(observed_ms, 6)},
                        )
                        return flight.result

                    # 3. No cache, no in-flight — we are the leader; register flight
                    self.dedup.begin_flight(cache_key)

                if self.rate_limiter_enabled and self.rate_limiter:
                    allowed, reason = self.rate_limiter.try_acquire(tool_name)
                    if not allowed:
                        self.metrics.record_rate_limit()
                        self._record_event(
                            "rate_limited",
                            agent_id,
                            tool_name,
                            {"args_hash": args_hash, "reason": reason},
                        )
                        # End flight so waiters get the error
                        if self.dedup_enabled and self.dedup:
                            err = RuntimeError(f"AgentGlue: rate limited ({reason})")
                            self.dedup.end_flight(cache_key, error=err)
                        raise RuntimeError(f"AgentGlue: rate limited ({reason})")

                self._record_event("tool_call", agent_id, tool_name, {"args_hash": args_hash})
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:
                    if self.dedup_enabled and self.dedup:
                        self.dedup.end_flight(cache_key, error=exc)
                    raise

                observed_ms = (time.monotonic() - started) * 1000.0
                self.metrics.record_tool_call(
                    deduped=False,
                    cache_hit=False,
                    latency_ms=observed_ms,
                    underlying_latency_ms=observed_ms,
                )

                if self.dedup_enabled and self.dedup:
                    self.dedup.store(tool_name, args, kwargs, result, agent_id=agent_id, ttl=ttl)
                    coalesced = self.dedup.end_flight(cache_key, result=result)
                    if coalesced:
                        self._record_event(
                            "tool_call_completed",
                            agent_id,
                            tool_name,
                            {"args_hash": args_hash, "latency_ms": round(observed_ms, 6), "coalesced_waiters": coalesced},
                        )
                    else:
                        self._record_event(
                            "tool_call_completed",
                            agent_id,
                            tool_name,
                            {"args_hash": args_hash, "latency_ms": round(observed_ms, 6)},
                        )
                else:
                    self._record_event(
                        "tool_call_completed",
                        agent_id,
                        tool_name,
                        {"args_hash": args_hash, "latency_ms": round(observed_ms, 6)},
                    )

                if self.memory_enabled and self.memory:
                    mem_key = f"{tool_name}:{args_hash}"
                    self.memory.write(mem_key, result, agent_id=agent_id)
                    self.metrics.record_memory_write()

                return result

            wrapper.__wrapped__ = func
            return wrapper

        return decorator

    def invalidate(self, tool_name: str, *args, **kwargs) -> bool:
        """Invalidate a single cached tool result."""
        if not self.dedup_enabled or not self.dedup:
            return False
        return self.dedup.invalidate(tool_name, args=args, kwargs=kwargs)

    def clear_cache(self) -> None:
        """Clear all cached tool results."""
        if self.dedup_enabled and self.dedup:
            self.dedup.clear()

    def report(self) -> str:
        return self.metrics.report()

    def summary(self) -> Dict:
        return self.metrics.summary()

    def export_events_jsonl(self, path: str) -> Dict[str, Any]:
        """Export the current recorder stream to JSONL and return a compact summary."""
        if not self.recorder:
            raise RuntimeError("AgentGlue recorder is disabled; initialize with record_events=True")
        exported = self.recorder.export_summary(path)
        return {
            **exported,
            "path": str(Path(exported["path"])),
            "metrics": self.summary(),
        }

    def _record_event(self, event_type: str, agent_id: str, tool_name: str, payload: Dict | None = None) -> None:
        if self.recorder:
            from agentglue.core.events import Event

            event = Event(
                event_type=event_type,
                agent_id=agent_id,
                tool_name=tool_name,
                payload=payload or {},
            )
            self.recorder.record(event.to_dict())

    @staticmethod
    def _hash_call(tool_name: str, args: tuple, kwargs: dict) -> str:
        raw = json.dumps({"tool": tool_name, "args": list(args), "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
