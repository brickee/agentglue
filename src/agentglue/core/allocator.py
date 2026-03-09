"""Resource allocator with token-bucket rate limiting and backpressure.

Used by AgentGlue's optional rate-coordination path to manage cross-agent
rate limits on shared tools/APIs.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class TokenBucket:
    rate_per_sec: float
    capacity: float
    tokens: float
    last_refill_time: float = 0.0

    def refill(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        elapsed = max(0.0, now - self.last_refill_time)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
        self.last_refill_time = now

    def consume(self, amount: float = 1.0, now: float | None = None) -> bool:
        now = now or time.monotonic()
        self.refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


class RateLimiter:
    """Cross-agent rate limiter for shared tools.

    Each tool can have a token bucket. Multiple agents share the same bucket,
    preventing collective rate limit violations.

    backpressure_policy:
      - wait: caller should retry later
      - drop: immediate rejection
      - retry: rejection with retry hint
    """

    def __init__(
        self,
        tool_rate_limits: Dict[str, float] | None = None,
        backpressure_policy: str = "retry",
    ):
        self.backpressure_policy = backpressure_policy
        self.tool_buckets: Dict[str, TokenBucket] = {}
        for tool_name, rps in (tool_rate_limits or {}).items():
            cap = max(1.0, rps)
            self.tool_buckets[tool_name] = TokenBucket(
                rate_per_sec=rps, capacity=cap, tokens=cap, last_refill_time=time.monotonic()
            )

    def try_acquire(self, tool_name: str) -> Tuple[bool, str]:
        """Try to acquire a rate limit token for a tool call.

        Returns (allowed, reason).
        """
        bucket = self.tool_buckets.get(tool_name)
        if bucket is None:
            return True, "no_limit"
        if bucket.consume():
            return True, "ok"
        return self._reject("rate_limited")

    def add_tool(self, tool_name: str, rate_per_sec: float) -> None:
        cap = max(1.0, rate_per_sec)
        self.tool_buckets[tool_name] = TokenBucket(
            rate_per_sec=rate_per_sec, capacity=cap, tokens=cap, last_refill_time=time.monotonic()
        )

    def _reject(self, reason: str) -> Tuple[bool, str]:
        if self.backpressure_policy == "drop":
            return False, f"dropped:{reason}"
        if self.backpressure_policy == "retry":
            return False, f"retry:{reason}"
        return False, f"wait:{reason}"
