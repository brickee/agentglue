"""Task lock and conflict prevention.

Allows agents to declare intent before starting work, preventing
multiple agents from working on the same task simultaneously.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class IntentEntry:
    task_id: str
    agent_id: str
    description: str = ""
    acquired_at: float = field(default_factory=time.monotonic)
    ttl: float = 300.0  # auto-release after 5 minutes

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.acquired_at) > self.ttl


class TaskLock:
    """Distributed intent declaration and conflict detection.

    Agents announce what they're about to do. If another agent has already
    claimed the same task, the conflict is detected before work begins.
    """

    def __init__(self, default_ttl: float = 300.0):
        self.default_ttl = default_ttl
        self._locks: Dict[str, IntentEntry] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        task_id: str,
        agent_id: str,
        description: str = "",
        ttl: float | None = None,
    ) -> Tuple[bool, str]:
        """Try to claim a task. Returns (success, reason)."""
        with self._lock:
            self._cleanup_expired()
            existing = self._locks.get(task_id)
            if existing is not None:
                if existing.agent_id == agent_id:
                    return True, "already_held"
                return False, f"conflict:held_by:{existing.agent_id}"
            self._locks[task_id] = IntentEntry(
                task_id=task_id,
                agent_id=agent_id,
                description=description,
                ttl=ttl or self.default_ttl,
            )
            return True, "acquired"

    def release(self, task_id: str, agent_id: str) -> bool:
        """Release a task lock. Only the holder can release."""
        with self._lock:
            entry = self._locks.get(task_id)
            if entry is None:
                return False
            if entry.agent_id != agent_id:
                return False
            del self._locks[task_id]
            return True

    def check(self, task_id: str) -> Optional[IntentEntry]:
        """Check who holds a task lock."""
        with self._lock:
            self._cleanup_expired()
            return self._locks.get(task_id)

    def held_by(self, agent_id: str) -> List[str]:
        """List all tasks held by an agent."""
        with self._lock:
            self._cleanup_expired()
            return [k for k, v in self._locks.items() if v.agent_id == agent_id]

    def _cleanup_expired(self) -> None:
        expired = [k for k, v in self._locks.items() if v.expired]
        for k in expired:
            del self._locks[k]

    @property
    def active_locks(self) -> int:
        with self._lock:
            self._cleanup_expired()
            return len(self._locks)
