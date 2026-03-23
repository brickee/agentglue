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
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
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

# Ensure ~/.local/bin is in PATH for rg (ripgrep) and other user-installed tools
_local_bin = os.path.expanduser("~/.local/bin")
if _local_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _local_bin + ":" + os.environ.get("PATH", "")


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
        pattern: str,
        path: str = ".",
        repo_path: str = "",
        glob: str = "",
        file_pattern: str = "",
        type: str = "",
        output_mode: str = "content",
        case_insensitive: bool = False,
        multiline: bool = False,
        context: int = 0,
        after_context: int = 0,
        before_context: int = 0,
        head_limit: int = 0,
        max_results: int = 0,
    ) -> str:
        import subprocess

        search_path = repo_path or path or "."
        cmd = ["rg", "--no-heading", "--line-number"]
        # Glob / file type filters
        glob_filter = glob or file_pattern
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        if type:
            cmd.extend(["--type", type])
        # Flags
        if case_insensitive:
            cmd.append("-i")
        if multiline:
            cmd.extend(["-U", "--multiline-dotall"])
        # Context
        if context:
            cmd.extend(["-C", str(context)])
        else:
            if after_context:
                cmd.extend(["-A", str(after_context)])
            if before_context:
                cmd.extend(["-B", str(before_context)])
        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        # Limit
        limit = head_limit or max_results
        if limit:
            cmd.extend(["--max-count", str(limit)])
        cmd.extend(["--", pattern, search_path])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 1:
                return f"No matches found for '{pattern}'"
            if result.returncode != 0:
                return f"Search error: {result.stderr}"
            return result.stdout.strip() or f"No matches found for '{pattern}'"
        except subprocess.TimeoutExpired:
            return "Error: Search timed out (30s limit)"
        except FileNotFoundError:
            return "Error: rg (ripgrep) not found; install it for search support"
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

    # ------------------------------------------------------------------
    # Cached exec: read-only command whitelist
    # ------------------------------------------------------------------

    # Commands that are safe (read-only, no side effects)
    EXEC_WHITELIST = {
        "git", "ls", "cat", "head", "tail", "wc", "file", "stat",
        "which", "env", "echo", "date", "uname", "whoami", "pwd",
        "find", "tree", "du", "df",
    }

    # Git sub-commands that are safe (read-only)
    GIT_SAFE_SUBCOMMANDS = {
        "log", "status", "diff", "blame", "show", "branch", "tag",
        "remote", "rev-parse", "describe", "shortlog", "stash list",
        "ls-files", "ls-tree", "config", "reflog",
    }

    # Patterns that indicate dangerous operations even inside whitelisted commands
    DANGEROUS_PATTERNS = re.compile(
        r"(?:"
        r"[>]{1,2}"       # redirections > or >>
        r"|[|]\s*(?:rm|mv|cp|dd|mkfs|shred|tee)\b"  # pipes to dangerous cmds
        r"|\brm\b"        # rm anywhere
        r"|\bmv\b"        # mv anywhere
        r"|\bcp\b"        # cp anywhere
        r"|\bsudo\b"      # sudo anywhere
        r"|\bchmod\b"     # chmod anywhere
        r"|\bchown\b"     # chown anywhere
        r"|\bkill\b"      # kill anywhere
        r"|\bdd\b"        # dd anywhere
        r"|\bmkdir\b"     # mkdir anywhere
        r"|\brmdir\b"     # rmdir anywhere
        r"|\btouch\b"     # touch anywhere
        r"|\btruncate\b"  # truncate anywhere
        r")",
        re.IGNORECASE,
    )

    def _validate_exec_command(command: str) -> tuple[bool, str]:
        """Validate a command against the read-only whitelist.

        Returns (allowed, reason).
        """
        command = command.strip()
        if not command:
            return False, "Empty command"

        # Check for dangerous patterns first
        if DANGEROUS_PATTERNS.search(command):
            return False, f"Command contains disallowed pattern (redirections, rm, mv, cp, sudo, etc.)"

        # Split on pipes — every segment must start with a whitelisted command
        segments = [s.strip() for s in command.split("|")]
        for seg in segments:
            if not seg:
                continue
            try:
                parts = shlex.split(seg)
            except ValueError:
                # Fall back to simple split if shlex fails
                parts = seg.split()
            if not parts:
                continue
            base_cmd = os.path.basename(parts[0])

            if base_cmd not in EXEC_WHITELIST:
                return False, f"Command '{base_cmd}' is not in the read-only whitelist. Allowed: {', '.join(sorted(EXEC_WHITELIST))}"

            # Extra validation for git: only allow safe sub-commands
            if base_cmd == "git" and len(parts) > 1:
                sub = parts[1]
                if sub.startswith("-"):
                    # flags like git --version are fine
                    pass
                elif sub not in GIT_SAFE_SUBCOMMANDS:
                    return False, f"Git sub-command '{sub}' is not allowed. Safe sub-commands: {', '.join(sorted(GIT_SAFE_SUBCOMMANDS))}"

        return True, "OK"

    # TTL mapping for exec commands — shorter for fast-changing commands
    def _exec_ttl(command: str) -> float:
        cmd = command.strip().lower()
        if cmd.startswith("git status"):
            return 10.0
        if cmd.startswith("git diff"):
            return 15.0
        if cmd.startswith("git log"):
            return 60.0
        if cmd.startswith("git"):
            return 30.0
        if cmd.startswith(("ls", "find", "tree")):
            return 15.0
        if cmd.startswith(("date", "env")):
            return 5.0
        return 30.0

    @g.tool(name="deduped_exec", ttl=30.0, rate_limit=5.0)
    def deduped_exec_tool(command: str, timeout: int = 30) -> str:
        allowed, reason = _validate_exec_command(command)
        if not allowed:
            return f"Error: Command not allowed. {reason}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=min(timeout, 60),
            )
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            if not output.strip():
                output = f"(command completed with exit code {result.returncode})"
            return output.strip()
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"
        except Exception as e:
            return f"Error executing command: {str(e)}"

    # ------------------------------------------------------------------
    # Cached web_fetch: HTTP GET via urllib
    # ------------------------------------------------------------------

    @g.tool(name="deduped_web_fetch", ttl=120.0, rate_limit=2.0)
    def deduped_web_fetch_tool(url: str, headers: str = "{}") -> str:
        """Fetch a URL and return its content."""
        try:
            parsed_headers = json.loads(headers) if isinstance(headers, str) else (headers or {})
        except (json.JSONDecodeError, TypeError):
            parsed_headers = {}

        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "AgentGlue/0.5 (OpenClaw sidecar)")
        for k, v in parsed_headers.items():
            req.add_header(str(k), str(v))

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read()
                # Try to decode as text
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                try:
                    body = data.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    body = data.decode("utf-8", errors="replace")
                # Truncate very large responses
                if len(body) > 100_000:
                    body = body[:100_000] + f"\n\n... (truncated, {len(data)} bytes total)"
                return f"HTTP {resp.status}\nContent-Type: {content_type}\n\n{body}"
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:5000]
            except Exception:
                pass
            return f"HTTP Error {e.code}: {e.reason}\n{body}"
        except urllib.error.URLError as e:
            return f"URL Error: {str(e.reason)}"
        except Exception as e:
            return f"Error fetching URL: {str(e)}"

    # ------------------------------------------------------------------
    # Cached web_search: stub (no API key in sidecar)
    # ------------------------------------------------------------------

    @g.tool(name="deduped_web_search", ttl=300.0, rate_limit=1.0)
    def deduped_web_search_tool(query: str, max_results: int = 5) -> str:
        """Web search stub. Real caching happens via the after_tool_call hook
        in TypeScript when the actual built-in web_search tool is used."""
        return (
            f"Web search not available in sidecar mode. "
            f"Query '{query}' (max_results={max_results}) should be routed "
            f"through the built-in web_search tool; results will be cached "
            f"automatically by the after_tool_call hook."
        )

    return {
        "search": search_tool,
        "metrics": metrics_tool,
        "deduped_search": deduped_search_tool,
        "deduped_read_file": deduped_read_file_tool,
        "deduped_list_files": deduped_list_files_tool,
        "deduped_exec": deduped_exec_tool,
        "deduped_web_fetch": deduped_web_fetch_tool,
        "deduped_web_search": deduped_web_search_tool,
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
        elif self.path == "/cache/invalidate":
            self._handle_cache_invalidate()
        elif self.path == "/cache/flush":
            self._handle_cache_flush()
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
            import time as _t
            t0 = _t.monotonic()
            result = fn(**params)
            elapsed_ms = (_t.monotonic() - t0) * 1000
            # Record metrics for /call executions (not just /cache/check)
            if glue.metrics:
                glue.metrics.record_tool_call(
                    deduped=False, cache_hit=False,
                    latency_ms=elapsed_ms, underlying_latency_ms=elapsed_ms,
                )
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

    def _handle_cache_invalidate(self):
        """Invalidate cache entries by tool name. Used after write operations."""
        req = self._read_body()
        if req is None:
            return
        tool_names = req.get("tool_names", [])
        if not tool_names or not isinstance(tool_names, list):
            self.send_json({"error": "Missing or invalid 'tool_names' list"}, 400)
            return
        deleted = 0
        if glue.dedup and hasattr(glue.dedup, '_backend'):
            backend = glue.dedup._backend
            if hasattr(backend, 'invalidate_by_tool'):
                deleted = backend.invalidate_by_tool(tool_names)
        self.send_json({"invalidated": deleted, "tool_names": tool_names})

    def _handle_cache_flush(self):
        """Clear all cached entries. Used by benchmarks for per-task isolation."""
        flushed = 0
        if glue.dedup and hasattr(glue.dedup, '_backend'):
            backend = glue.dedup._backend
            if hasattr(backend, 'clear'):
                backend.clear()
                flushed = 1
        # Also reset metrics counters so each task starts fresh
        if glue.metrics:
            glue.metrics.reset()
        self.send_json({"flushed": True, "cleared": flushed})


def run_server(host: str = DEFAULT_HOST, port: int = 8765, db_path: str = DEFAULT_DB_PATH):
    global glue, TOOLS
    glue = create_glue(db_path)
    TOOLS = _register_tools(glue)

    server = HTTPServer((host, port), SidecarHandler)
    print(f"[AgentGlue Sidecar v0.3] http://{host}:{port}")
    print(f"[AgentGlue Sidecar] Backend: sqlite ({db_path})")
    print(f"[AgentGlue Sidecar] Tools: {', '.join(TOOLS.keys())}")
    print(f"[AgentGlue Sidecar] Endpoints: /health /call /cache/check /cache/store /cache/stats /cache/flush")

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
