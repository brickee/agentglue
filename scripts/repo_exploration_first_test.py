#!/usr/bin/env python3
"""First real AgentGlue repo-exploration test.

Runs a small deterministic multi-agent-style workload against a self-contained
benchmark fixture using real shell-backed tools:
- list_files
- search_code
- read_file

Compares a baseline run (no middleware) against an AgentGlue-wrapped run and
writes machine-readable artifacts for the markdown summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentglue import AgentGlue  # noqa: E402
from agentglue.core.recorder import detect_duplicates  # noqa: E402

TARGET_REPO = REPO_ROOT / "tests" / "benchmark_fixture"
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "first_test_2026-03-09"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def run_shell(command: str) -> str:
    result = subprocess.run(
        ["/bin/bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class ExecLogger:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def log(self, tool_name: str, args: Dict[str, Any], command: str, output: str, elapsed_ms: float) -> None:
        self.rows.append(
            {
                "tool_name": tool_name,
                "args": args,
                "command": command,
                "elapsed_ms": round(elapsed_ms, 3),
                "output_preview": output[:300],
            }
        )


def make_tools(exec_logger: ExecLogger) -> Tuple[Callable[..., str], Callable[..., str], Callable[..., str]]:
    def list_files(path: str, max_entries: int = 40) -> str:
        started = time.monotonic()
        cmd = (
            f"cd {TARGET_REPO} && "
            f"find {json.dumps(path)} -type f | sed 's#^./##' | sort | head -n {int(max_entries)}"
        )
        out = run_shell(cmd)
        exec_logger.log("list_files", {"path": path, "max_entries": max_entries}, cmd, out, (time.monotonic() - started) * 1000)
        return out

    def search_code(pattern: str, scope: str = "src tests", max_hits: int = 20) -> str:
        started = time.monotonic()
        cmd = (
            f"cd {TARGET_REPO} && "
            f"grep -RIn --binary-files=without-match -E {json.dumps(pattern)} {scope} | head -n {int(max_hits)}"
        )
        out = run_shell(cmd)
        exec_logger.log(
            "search_code",
            {"pattern": pattern, "scope": scope, "max_hits": max_hits},
            cmd,
            out,
            (time.monotonic() - started) * 1000,
        )
        return out

    def read_file(path: str, start_line: int = 1, end_line: int = 120) -> str:
        started = time.monotonic()
        cmd = (
            f"cd {TARGET_REPO} && "
            f"sed -n '{int(start_line)},{int(end_line)}p' {json.dumps(path)}"
        )
        out = run_shell(cmd)
        exec_logger.log(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
            cmd,
            out,
            (time.monotonic() - started) * 1000,
        )
        return out

    return list_files, search_code, read_file


AGENT_PLANS: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {
    "agent-a": [
        ("list_files", {"path": "src/coordination_demo/core", "max_entries": 20}),
        ("search_code", {"pattern": "TokenBucket|rate_limit", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/core/allocator.py", "start_line": 1, "end_line": 120}),
        ("search_code", {"pattern": "replay_duplicate_decomposition|replay_invariant_precheck", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 1, "end_line": 140}),
    ],
    "agent-b": [
        ("list_files", {"path": "src/coordination_demo/core", "max_entries": 20}),
        ("search_code", {"pattern": "TokenBucket|rate_limit", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/core/allocator.py", "start_line": 1, "end_line": 120}),
        ("search_code", {"pattern": "replay_duplicate_decomposition|replay_invariant_precheck", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 1, "end_line": 140}),
    ],
    "agent-c": [
        ("list_files", {"path": "src/coordination_demo/policies", "max_entries": 20}),
        ("search_code", {"pattern": "SharedMemoryPolicy|plan_semantic_duplicates", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/policies/shared_memory.py", "start_line": 1, "end_line": 120}),
        ("search_code", {"pattern": "semantic_duplicate_work_count|duplicate_tool_calls", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/eval/runner.py", "start_line": 320, "end_line": 420}),
    ],
    "agent-d": [
        ("list_files", {"path": "src/coordination_demo/core", "max_entries": 20}),
        ("search_code", {"pattern": "semantic_duplicate_work_count|duplicate_tool_calls", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/eval/runner.py", "start_line": 320, "end_line": 420}),
        ("search_code", {"pattern": "EventRecorder|replay_event_distribution", "scope": "src tests", "max_hits": 20}),
        ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 1, "end_line": 140}),
    ],
}


def freeze(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True)


def analyze_observed_duplicates(plans: Dict[str, List[Tuple[str, Dict[str, Any]]]]) -> Dict[str, Any]:
    intent_agents: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    per_tool = Counter()
    for agent_id, steps in plans.items():
        for tool_name, kwargs in steps:
            key = (tool_name, freeze(kwargs))
            intent_agents[key].append(agent_id)
    duplicate_intents = []
    for (tool_name, args_key), agents in intent_agents.items():
        if len(agents) > 1:
            duplicate_count = len(agents) - 1
            per_tool[tool_name] += duplicate_count
            duplicate_intents.append(
                {
                    "tool_name": tool_name,
                    "args": json.loads(args_key),
                    "agents": agents,
                    "duplicates": duplicate_count,
                }
            )
    duplicate_intents.sort(key=lambda x: (-x["duplicates"], x["tool_name"], json.dumps(x["args"], sort_keys=True)))
    return {
        "duplicate_intents": duplicate_intents,
        "duplicates_by_tool": dict(per_tool),
        "total_duplicates": sum(item["duplicates"] for item in duplicate_intents),
    }


def run_baseline() -> Dict[str, Any]:
    exec_logger = ExecLogger()
    list_files, search_code, read_file = make_tools(exec_logger)
    tool_map = {
        "list_files": list_files,
        "search_code": search_code,
        "read_file": read_file,
    }

    observed = []
    started = time.monotonic()
    for agent_id, steps in AGENT_PLANS.items():
        for tool_name, kwargs in steps:
            out = tool_map[tool_name](**kwargs)
            observed.append({
                "agent_id": agent_id,
                "tool_name": tool_name,
                "args": kwargs,
                "output_preview": out[:200],
            })
    elapsed_ms = (time.monotonic() - started) * 1000.0
    duplicate_analysis = analyze_observed_duplicates(AGENT_PLANS)
    return {
        "mode": "baseline",
        "observed_tool_calls": len(observed),
        "underlying_executions": len(exec_logger.rows),
        "wall_clock_ms": round(elapsed_ms, 3),
        "observed_calls": observed,
        "underlying_exec_log": exec_logger.rows,
        "duplicate_analysis": duplicate_analysis,
    }


def run_agentglue() -> Dict[str, Any]:
    exec_logger = ExecLogger()
    base_list_files, base_search_code, base_read_file = make_tools(exec_logger)

    glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False, dedup_ttl=600)
    list_files = glue.tool(ttl=600)(base_list_files)
    search_code = glue.tool(ttl=600)(base_search_code)
    read_file = glue.tool(ttl=600)(base_read_file)
    tool_map = {
        "list_files": list_files,
        "search_code": search_code,
        "read_file": read_file,
    }

    observed = []
    started = time.monotonic()
    for agent_id, steps in AGENT_PLANS.items():
        for tool_name, kwargs in steps:
            out = tool_map[tool_name](agent_id=agent_id, **kwargs)
            observed.append({
                "agent_id": agent_id,
                "tool_name": tool_name,
                "args": kwargs,
                "output_preview": out[:200],
            })
    elapsed_ms = (time.monotonic() - started) * 1000.0

    events = glue.recorder.events if glue.recorder else []
    duplicate_analysis = detect_duplicates(events)
    summary = glue.summary()
    summary["wall_clock_ms"] = round(elapsed_ms, 3)

    return {
        "mode": "agentglue",
        "summary": summary,
        "report": glue.report(),
        "events": events,
        "observed_tool_calls": len(observed),
        "underlying_executions": len(exec_logger.rows),
        "observed_calls": observed,
        "underlying_exec_log": exec_logger.rows,
        "duplicate_analysis": duplicate_analysis,
    }


def main() -> None:
    baseline = run_baseline()
    glue_run = run_agentglue()

    result = {
        "date": "2026-03-09",
        "target_repo": str(TARGET_REPO),
        "target_repo_file_count": len(list(TARGET_REPO.glob("src/**/*.py"))),
        "agent_count": len(AGENT_PLANS),
        "steps_per_agent": {k: len(v) for k, v in AGENT_PLANS.items()},
        "plans": AGENT_PLANS,
        "baseline": baseline,
        "agentglue": glue_run,
    }

    out_path = ARTIFACT_DIR / "repo_exploration_first_test.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    events_path = ARTIFACT_DIR / "agentglue_events.jsonl"
    with events_path.open("w", encoding="utf-8") as f:
        for event in glue_run["events"]:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(json.dumps({
        "result_json": str(out_path),
        "events_jsonl": str(events_path),
        "baseline_observed": baseline["observed_tool_calls"],
        "baseline_underlying": baseline["underlying_executions"],
        "glue_summary": glue_run["summary"],
        "glue_underlying": glue_run["underlying_executions"],
    }, indent=2))


if __name__ == "__main__":
    main()
