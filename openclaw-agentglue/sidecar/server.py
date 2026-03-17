#!/usr/bin/env python3
"""
AgentGlue Sidecar Server v0.3

HTTP server that wraps tools with AgentGlue middleware.
Receives tool calls from OpenClaw plugin via HTTP/JSON.

v0.3: SQLite-backed cross-process dedup cache with /cache/* endpoints.
"""

import hashlib
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add bundled AgentGlue Python lib to path.
# Layout: sidecar/ -> (parent) openclaw-agentglue/ -> python/agentglue/
# Fallback: dev layout  sidecar/ -> openclaw-agentglue/ -> (parent) AgentGlue/ -> src/
SIDECAR_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SIDECAR_DIR.parent
BUNDLED_PYTHON = PLUGIN_DIR / "python"
DEV_SRC = PLUGIN_DIR.parent / "src"

if BUNDLED_PYTHON.is_dir():
    sys.path.insert(0, str(BUNDLED_PYTHON))
elif DEV_SRC.is_dir():
    sys.path.insert(0, str(DEV_SRC))

from agentglue import AgentGlue

# Default DB path — configurable via --db-path CLI arg
DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/cache/agentglue.db")
DEFAULT_HOST = "127.0.0.1"


def create_glue(db_path: str = DEFAULT_DB_PATH) -> AgentGlue:
    return AgentGlue(
        dedup=True,
        dedup_ttl=300.0,
        backend="sqlite",
        db_path=db_path,
        shared_memory=True,
        memory_ttl=600.0,
        rate_limiter=True,
        rate_limits={"search": 10.0},
        record_events=True,
    )


glue: AgentGlue = None  # initialized in run_server


# ---------------------------------------------------------------------------
# Tools wrapped with AgentGlue middleware
# ---------------------------------------------------------------------------

@staticmethod
def _register_tools(g: AgentGlue):
    """Register tools on the given AgentGlue instance."""

    @g.tool(name="search", ttl=60.0, rate_limit=10.0)
    def search_tool(query: str) -> str:
        results = [
            f"Result 1: Information about '{query}' from source A",
            f"Result 2: Details on '{query}' from source B",
            f"Result 3: Analysis of '{query}' from source C",
        ]
        return "\n".join(results)

    @g.tool(name="metrics")
    def metrics_tool() -> str:
        return g.report()

    @g.tool(name="deduped_search", ttl=30.0, rate_limit=5.0)
    def deduped_search_tool(
        repo_path: str,
        pattern: str,
        file_pattern: str = "*",
        max_results: int = 50,
    ) -> str:
        import subprocess

        if not os.path.isdir(repo_path):
            return f"Error: Directory not found: {repo_path}"
        try:
            cmd = ["grep", "-r", "-n", "--include", file_pattern, "-l", pattern, repo_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 1:
                return f"No files found matching pattern '{pattern}' in {repo_path}"
            if result.returncode != 0:
                return f"Search error: {result.stderr}"
            files = result.stdout.strip().split("\n")[:max_results]
            if not files or files == [""]:
                return f"No files found matching pattern '{pattern}'"
            output_lines = [f"Found {len(files)} file(s) matching '{pattern}':\n"]
            for f in files:
                line_cmd = ["grep", "-n", "-C", "2", pattern, f]
                lr = subprocess.run(line_cmd, capture_output=True, text=True, timeout=10)
                matches = lr.stdout.strip() if lr.returncode == 0 else "(error reading file)"
                if len(matches) > 500:
                    matches = matches[:500] + "...\n[truncated]"
                output_lines.append(f"\n=== {f} ===\n{matches}")
            return "\n".join(output_lines)
        except subprocess.TimeoutExpired:
            return "Error: Search timed out (30s limit)"
        except Exception as e:
            return f"Error during search: {str(e)}"

    @g.tool(name="deduped_read_file", ttl=60.0, rate_limit=10.0)
    def deduped_read_file_tool(file_path: str, offset: int = 1, limit: int = 200) -> str:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File not found: {file_path}"
        if not path.is_file():
            return f"Error: Path is not a file: {file_path}"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            start = max(0, offset - 1)
            end = min(start + limit, total)
            header = f"File: {file_path}\nLines: {start + 1}-{end} of {total}\n{'=' * 50}\n"
            numbered = [f"{i:4d}: {line}" for i, line in enumerate(lines[start:end], start=start + 1)]
            return header + "".join(numbered)
        except Exception as e:
            return f"Error reading file: {str(e)}"

    @g.tool(name="deduped_list_files", ttl=10.0, rate_limit=5.0)
    def deduped_list_files_tool(dir_path: str, recursive: bool = False, include_hidden: bool = False) -> str:
        path = Path(dir_path)
        if not path.exists():
            return f"Error: Directory not found: {dir_path}"
        if not path.is_dir():
            return f"Error: Path is not a directory: {dir_path}"
        try:
            items = list(path.rglob("*")) if recursive else list(path.iterdir())
            if not include_hidden:
                items = [i for i in items if not i.name.startswith(".")]
            items.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
            lines = [f"Directory: {dir_path}{' (recursive)' if recursive else ''}\n{'=' * 50}\n"]
            for item in items:
                prefix = "d " if item.is_dir() else "f "
                suffix = "/" if item.is_dir() else ""
                lines.append(f"{prefix}{item.relative_to(path)}{suffix}")
            lines.append(f"\nTotal: {len(items)} items")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    return {
        "search": search_tool,
        "metrics": metrics_tool,
        "deduped_search": deduped_search_tool,
        "deduped_read_file": deduped_read_file_tool,
        "deduped_list_files": deduped_list_files_tool,
    }


# Will be populated in run_server
TOOLS: dict = {}


def _make_cache_key(tool: str, params: dict) -> str:
    raw = json.dumps({"tool": tool, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class SidecarHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[Sidecar] {fmt % args}")

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self.send_json({"error": "Empty request body"}, 400)
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError as e:
            self.send_json({"error": f"Invalid JSON: {e}"}, 400)
            return None

    def do_GET(self):
        if self.path == "/health":
            from agentglue import __version__ as v
            self.send_json({
                "status": "ok",
                "agentglue_version": v,
                "backend": "sqlite",
                "tools": list(TOOLS.keys()),
            })
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/call":
            self._handle_call()
        elif self.path == "/cache/check":
            self._handle_cache_check()
        elif self.path == "/cache/store":
            self._handle_cache_store()
        elif self.path == "/cache/stats":
            self._handle_cache_stats()
        else:
            self.send_json({"error": "Not found"}, 404)

    def _handle_call(self):
        req = self._read_body()
        if req is None:
            return
        tool = req.get("tool")
        params = req.get("params", {})
        if not tool:
            self.send_json({"error": "Missing 'tool' field"}, 400)
            return
        fn = TOOLS.get(tool)
        if fn is None:
            self.send_json({"error": f"Unknown tool: {tool}"}, 400)
            return
        try:
            result = fn(**params)
            self.send_json({"result": result})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def _handle_cache_check(self):
        """Check if a tool+params combo is cached. Returns {hit, result} or {miss}."""
        req = self._read_body()
        if req is None:
            return
        tool = req.get("tool", "")
        params = req.get("params", {})
        if not tool:
            self.send_json({"error": "Missing 'tool'"}, 400)
            return
        # Build args tuple matching how the cache key is made
        key = _make_cache_key(tool, params)
        # Direct SQLite lookup via the backend
        if glue.dedup:
            entry = glue.dedup._backend.lookup(key)
            if entry is not None:
                glue.metrics.record_tool_call(deduped=True, cache_hit=True, latency_ms=0)
                self.send_json({"hit": True, "result": entry.result, "age_s": round(entry.age, 2)})
                return
        self.send_json({"hit": False})

    def _handle_cache_store(self):
        """Store a tool result in the cache. Used by after_tool_call hook."""
        req = self._read_body()
        if req is None:
            return
        tool = req.get("tool", "")
        params = req.get("params", {})
        result = req.get("result")
        ttl = req.get("ttl", 300.0)
        agent_id = req.get("agent_id", "")
        if not tool:
            self.send_json({"error": "Missing 'tool'"}, 400)
            return
        if result is None:
            self.send_json({"error": "Missing 'result'"}, 400)
            return
        key = _make_cache_key(tool, params)
        if glue.dedup:
            import time
            from agentglue.middleware.dedup import CacheEntry
            entry = CacheEntry(
                result=result,
                created_at=time.time(),
                ttl=float(ttl),
                tool_name=tool,
                args_hash=key,
                agent_id=agent_id,
            )
            entry._use_wall_clock = True
            glue.dedup._backend.store(key, entry)
        self.send_json({"stored": True})

    def _handle_cache_stats(self):
        """Return cache statistics."""
        stats = glue.summary()
        if glue.dedup:
            stats["cache_size"] = glue.dedup.size
            stats["backend"] = glue.dedup.backend_type
        self.send_json(stats)


def run_server(host: str = DEFAULT_HOST, port: int = 8765, db_path: str = DEFAULT_DB_PATH):
    global glue, TOOLS
    glue = create_glue(db_path)
    TOOLS = _register_tools(glue)

    server = HTTPServer((host, port), SidecarHandler)
    print(f"[AgentGlue Sidecar v0.3] http://{host}:{port}")
    print(f"[AgentGlue Sidecar] Backend: sqlite ({db_path})")
    print(f"[AgentGlue Sidecar] Tools: {', '.join(TOOLS.keys())}")
    print(f"[AgentGlue Sidecar] Endpoints: /health /call /cache/check /cache/store /cache/stats")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[AgentGlue Sidecar] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentGlue Sidecar Server v0.3")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help="Host address to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH, help="SQLite database path")
    args = parser.parse_args()

    run_server(args.host, args.port, args.db_path)
