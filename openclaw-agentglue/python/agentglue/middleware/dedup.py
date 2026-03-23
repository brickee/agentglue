"""Tool call deduplication middleware.

Intercepts tool calls and returns cached results when the same tool
has been called with the same arguments.  Supports in-flight coalescing
(single-flight): if an identical call is already executing, later callers
wait for the first result instead of executing again.

v0.3: Added SQLite backend for cross-process cache sharing.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional


@dataclass
class CacheEntry:
    result: Any
    created_at: float
    ttl: float
    tool_name: str
    args_hash: str
    agent_id: str

    @property
    def expired(self) -> bool:
        if self._use_wall_clock:
            return (time.time() - self.created_at) > self.ttl
        return (time.monotonic() - self.created_at) > self.ttl

    @property
    def age(self) -> float:
        if self._use_wall_clock:
            return time.time() - self.created_at
        return time.monotonic() - self.created_at

    _use_wall_clock: bool = False


@dataclass
class _InFlight:
    """Tracks a single in-progress tool execution for coalescing."""
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    waiters: int = 0


class _MemoryBackend:
    """In-memory cache backend (original v0.1 behavior)."""

    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def lookup(self, key: str) -> Optional[CacheEntry]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expired:
                del self._cache[key]
                return None
            return entry

    def store(self, key: str, entry: CacheEntry) -> None:
        with self._lock:
            self._cache[key] = entry

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        with self._lock:
            stale = [k for k, v in self._cache.items() if v.expired]
            for k in stale:
                del self._cache[k]
            return len(self._cache)


class _SqliteBackend:
    """SQLite-backed cache for cross-process sharing (v0.3).

    Uses WAL mode for concurrent readers.  Stores results as JSON.
    Timestamps use wall-clock time (time.time()) so they are valid
    across processes.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS dedup_cache (
        key        TEXT PRIMARY KEY,
        result     TEXT NOT NULL,
        tool_name  TEXT NOT NULL,
        args_hash  TEXT NOT NULL,
        agent_id   TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        ttl        REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_expires ON dedup_cache (created_at, ttl);
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self._write_count = 0
        # Initialize schema on the first connection
        conn = self._conn()
        conn.executescript(self._SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection (sqlite3 objects are thread-bound)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def lookup(self, key: str) -> Optional[CacheEntry]:
        now = time.time()
        row = self._conn().execute(
            "SELECT result, tool_name, args_hash, agent_id, created_at, ttl "
            "FROM dedup_cache WHERE key = ? AND (created_at + ttl) > ?",
            (key, now),
        ).fetchone()
        if row is None:
            return None
        result_json, tool_name, args_hash, agent_id, created_at, ttl = row
        entry = CacheEntry(
            result=json.loads(result_json),
            created_at=created_at,
            ttl=ttl,
            tool_name=tool_name,
            args_hash=args_hash,
            agent_id=agent_id,
        )
        entry._use_wall_clock = True
        return entry

    def store(self, key: str, entry: CacheEntry) -> None:
        result_json = json.dumps(entry.result, default=str)
        # Use wall-clock time for cross-process validity
        created_at = time.time()
        self._conn().execute(
            "INSERT OR REPLACE INTO dedup_cache "
            "(key, result, tool_name, args_hash, agent_id, created_at, ttl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, result_json, entry.tool_name, entry.args_hash,
             entry.agent_id, created_at, entry.ttl),
        )
        self._conn().commit()
        self._write_count += 1
        if self._write_count % 100 == 0:
            self._cleanup_expired()

    def delete(self, key: str) -> bool:
        cur = self._conn().execute("DELETE FROM dedup_cache WHERE key = ?", (key,))
        self._conn().commit()
        return cur.rowcount > 0

    def invalidate_by_tool(self, tool_names: list[str]) -> int:
        """Delete all cache entries whose tool_name is in the given list."""
        if not tool_names:
            return 0
        placeholders = ",".join("?" for _ in tool_names)
        cur = self._conn().execute(
            f"DELETE FROM dedup_cache WHERE tool_name IN ({placeholders})",
            tool_names,
        )
        self._conn().commit()
        return cur.rowcount

    def clear(self) -> None:
        self._conn().execute("DELETE FROM dedup_cache")
        self._conn().commit()

    def size(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM dedup_cache WHERE (created_at + ttl) > ?",
            (time.time(),),
        ).fetchone()
        return row[0] if row else 0

    def _cleanup_expired(self) -> int:
        cur = self._conn().execute(
            "DELETE FROM dedup_cache WHERE (created_at + ttl) <= ?",
            (time.time(),),
        )
        self._conn().commit()
        return cur.rowcount


class ToolDedup:
    """Deduplicates tool calls across multiple agents.

    Exact-match dedup via a stable hash of tool name + serialized args/kwargs
    with TTL-based caching.  Also supports single-flight coalescing: concurrent
    identical calls share the result of the first execution.

    v0.3: Set backend="sqlite" and db_path for cross-process cache sharing.
    """

    def __init__(
        self,
        default_ttl: float = 300.0,
        backend: Literal["memory", "sqlite"] = "memory",
        db_path: str | None = None,
    ):
        self.default_ttl = default_ttl
        self.backend_type = backend

        if backend == "sqlite":
            if db_path is None:
                db_path = os.path.expanduser("~/.openclaw/cache/agentglue.db")
            self._backend = _SqliteBackend(db_path)
        else:
            self._backend = _MemoryBackend()

        # In-flight tracking is always in-memory (per-process)
        self._lock = threading.Lock()
        self._flights: Dict[str, _InFlight] = {}

    def _make_key(self, tool_name: str, args: tuple, kwargs: dict) -> str:
        raw = json.dumps({"tool": tool_name, "args": list(args), "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    # -- single-flight helpers --------------------------------------------------

    def try_join_flight(self, key: str) -> Optional[_InFlight]:
        """If an identical call is already in-flight, register as a waiter and return the flight."""
        with self._lock:
            flight = self._flights.get(key)
            if flight is not None:
                flight.waiters += 1
                return flight
            return None

    def begin_flight(self, key: str) -> _InFlight:
        """Register a new in-flight execution.  Must be called under no existing flight for *key*."""
        flight = _InFlight()
        with self._lock:
            self._flights[key] = flight
        return flight

    def end_flight(self, key: str, result: Any = None, error: BaseException | None = None) -> int:
        """Complete an in-flight execution, wake waiters, and remove the flight.

        Returns the number of waiters that were coalesced.
        """
        with self._lock:
            flight = self._flights.pop(key, None)
        if flight is None:
            return 0
        flight.result = result
        flight.error = error
        waiters = flight.waiters
        flight.event.set()
        return waiters

    # -- cache operations -------------------------------------------------------

    def lookup(self, tool_name: str, args: tuple, kwargs: dict) -> Optional[CacheEntry]:
        key = self._make_key(tool_name, args, kwargs)
        return self._backend.lookup(key)

    def store(
        self,
        tool_name: str,
        args: tuple,
        kwargs: dict,
        result: Any,
        agent_id: str = "",
        ttl: float | None = None,
    ) -> None:
        key = self._make_key(tool_name, args, kwargs)
        use_wall = self.backend_type == "sqlite"
        entry = CacheEntry(
            result=result,
            created_at=time.time() if use_wall else time.monotonic(),
            ttl=self.default_ttl if ttl is None else ttl,
            tool_name=tool_name,
            args_hash=key,
            agent_id=agent_id,
        )
        entry._use_wall_clock = use_wall
        self._backend.store(key, entry)

    def invalidate(self, tool_name: str, args: tuple = (), kwargs: dict | None = None) -> bool:
        key = self._make_key(tool_name, args, kwargs or {})
        return self._backend.delete(key)

    def clear(self) -> None:
        self._backend.clear()

    @property
    def size(self) -> int:
        return self._backend.size()

    def wrap(
        self,
        func: Callable,
        tool_name: str | None = None,
        ttl: float | None = None,
    ) -> Callable:
        """Wrap a tool function with dedup logic."""
        name = tool_name or func.__name__

        def wrapper(*args, **kwargs):
            entry = self.lookup(name, args, kwargs)
            if entry is not None:
                return entry.result

            result = func(*args, **kwargs)
            self.store(name, args, kwargs, result, ttl=ttl)
            return result

        wrapper.__name__ = func.__name__
        wrapper.__wrapped__ = func
        return wrapper
