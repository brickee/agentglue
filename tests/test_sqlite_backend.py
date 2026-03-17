"""Tests for SQLite-backed dedup cache (v0.3)."""

import os
import subprocess
import sys
import time

import pytest

# Ensure src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentglue.middleware.dedup import ToolDedup, _SqliteBackend


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_dedup.db")


@pytest.fixture
def sqlite_dedup(db_path):
    return ToolDedup(default_ttl=10.0, backend="sqlite", db_path=db_path)


class TestSqliteBackend:
    def test_store_and_lookup(self, sqlite_dedup):
        sqlite_dedup.store("search", ("hello",), {}, "result1", agent_id="a1")
        entry = sqlite_dedup.lookup("search", ("hello",), {})
        assert entry is not None
        assert entry.result == "result1"
        assert entry.agent_id == "a1"

    def test_cache_miss(self, sqlite_dedup):
        entry = sqlite_dedup.lookup("search", ("nonexistent",), {})
        assert entry is None

    def test_ttl_expiry(self, db_path):
        dedup = ToolDedup(default_ttl=0.1, backend="sqlite", db_path=db_path)
        dedup.store("search", ("q",), {}, "old_result")
        time.sleep(0.2)
        entry = dedup.lookup("search", ("q",), {})
        assert entry is None, "Expired entries should not be returned"

    def test_invalidate(self, sqlite_dedup):
        sqlite_dedup.store("read", ("file.py",), {}, "contents")
        assert sqlite_dedup.invalidate("read", args=("file.py",))
        entry = sqlite_dedup.lookup("read", ("file.py",), {})
        assert entry is None

    def test_invalidate_nonexistent(self, sqlite_dedup):
        assert not sqlite_dedup.invalidate("nope")

    def test_clear(self, sqlite_dedup):
        sqlite_dedup.store("a", ("1",), {}, "r1")
        sqlite_dedup.store("b", ("2",), {}, "r2")
        assert sqlite_dedup.size >= 2
        sqlite_dedup.clear()
        assert sqlite_dedup.size == 0

    def test_size_excludes_expired(self, db_path):
        dedup = ToolDedup(default_ttl=0.1, backend="sqlite", db_path=db_path)
        dedup.store("a", ("1",), {}, "r1")
        dedup.store("b", ("2",), {}, "r2")
        time.sleep(0.2)
        assert dedup.size == 0

    def test_overwrite_existing(self, sqlite_dedup):
        sqlite_dedup.store("s", ("q",), {}, "v1")
        sqlite_dedup.store("s", ("q",), {}, "v2")
        entry = sqlite_dedup.lookup("s", ("q",), {})
        assert entry.result == "v2"

    def test_complex_result_types(self, sqlite_dedup):
        result = {"files": ["a.py", "b.py"], "count": 2, "nested": {"key": True}}
        sqlite_dedup.store("search", ("q",), {"limit": 10}, result)
        entry = sqlite_dedup.lookup("search", ("q",), {"limit": 10})
        assert entry.result == result

    def test_different_args_different_entries(self, sqlite_dedup):
        sqlite_dedup.store("read", ("a.py",), {}, "content_a")
        sqlite_dedup.store("read", ("b.py",), {}, "content_b")
        assert sqlite_dedup.lookup("read", ("a.py",), {}).result == "content_a"
        assert sqlite_dedup.lookup("read", ("b.py",), {}).result == "content_b"


class TestCrossProcess:
    def test_cross_process_cache_sharing(self, db_path):
        """Parent stores a value; child subprocess reads it."""
        dedup = ToolDedup(default_ttl=60.0, backend="sqlite", db_path=db_path)
        dedup.store("search", ("cross_process_test",), {}, "shared_value")

        # Child process reads from the same DB
        child_code = f"""
import sys, os, json
sys.path.insert(0, os.path.join("{os.path.dirname(__file__)}", "..", "src"))
from agentglue.middleware.dedup import ToolDedup
dedup = ToolDedup(default_ttl=60.0, backend="sqlite", db_path="{db_path}")
entry = dedup.lookup("search", ("cross_process_test",), {{}})
if entry and entry.result == "shared_value":
    print("PASS")
else:
    print("FAIL:" + str(entry))
"""
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=10,
        )
        assert result.stdout.strip() == "PASS", f"Child output: {result.stdout} {result.stderr}"

    def test_cross_process_store_and_read(self, db_path):
        """Child stores a value; parent reads it."""
        child_code = f"""
import sys, os
sys.path.insert(0, os.path.join("{os.path.dirname(__file__)}", "..", "src"))
from agentglue.middleware.dedup import ToolDedup
dedup = ToolDedup(default_ttl=60.0, backend="sqlite", db_path="{db_path}")
dedup.store("tool_x", ("arg1",), {{"k": "v"}}, "child_wrote_this")
print("STORED")
"""
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=10,
        )
        assert "STORED" in result.stdout, f"Child failed: {result.stderr}"

        dedup = ToolDedup(default_ttl=60.0, backend="sqlite", db_path=db_path)
        entry = dedup.lookup("tool_x", ("arg1",), {"k": "v"})
        assert entry is not None
        assert entry.result == "child_wrote_this"


class TestBackwardCompat:
    def test_memory_backend_still_works(self):
        dedup = ToolDedup(default_ttl=5.0, backend="memory")
        dedup.store("t", ("a",), {}, "val")
        assert dedup.lookup("t", ("a",), {}).result == "val"
        dedup.clear()
        assert dedup.size == 0

    def test_default_backend_is_memory(self):
        dedup = ToolDedup()
        assert dedup.backend_type == "memory"

    def test_runtime_with_sqlite(self, db_path):
        from agentglue import AgentGlue
        glue = AgentGlue(dedup=True, backend="sqlite", db_path=db_path)

        @glue.tool(name="test_tool")
        def my_tool(q: str) -> str:
            return f"result_{q}"

        r1 = my_tool("hello")
        assert r1 == "result_hello"

        # Second call should be deduped
        r2 = my_tool("hello")
        assert r2 == "result_hello"
        assert glue.metrics.tool_calls_deduped >= 1
