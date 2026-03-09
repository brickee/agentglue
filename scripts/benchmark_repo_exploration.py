#!/usr/bin/env python3
"""Lightweight benchmark harness for AgentGlue repo-exploration scenarios.

Keeps the scope narrow:
- exact-match dedup + TTL cache
- benchmark repeatability across multiple runs
- stable JSON artifacts with clear metadata
- one concurrency probe to show cache-after-first-call vs in-flight coalescing
- one partial-overlap scenario to show where exact-match dedup stops helping
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentglue import AgentGlue  # noqa: E402
from agentglue.core.recorder import detect_duplicates  # noqa: E402

DEFAULT_TARGET_REPO = REPO_ROOT / "tests" / "benchmark_fixture"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "benchmarks"
DEFAULT_SCENARIOS = ["repo_exploration", "partial_overlap"]

SCENARIO_PLANS: Dict[str, Dict[str, List[Tuple[str, Dict[str, Any]]]]] = {
    "repo_exploration": {
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
    },
    "partial_overlap": {
        "agent-a": [
            ("list_files", {"path": "src/coordination_demo/core", "max_entries": 20}),
            ("search_code", {"pattern": "TokenBucket|rate_limit", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/core/allocator.py", "start_line": 1, "end_line": 120}),
            ("search_code", {"pattern": "replay_duplicate_decomposition|replay_invariant_precheck", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 1, "end_line": 140}),
        ],
        "agent-b": [
            ("list_files", {"path": "src/coordination_demo/core", "max_entries": 20}),
            ("search_code", {"pattern": "rate_limit|rate_limited", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/core/allocator.py", "start_line": 40, "end_line": 160}),
            ("search_code", {"pattern": "replay_duplicate_decomposition|replay_event_distribution", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 60, "end_line": 180}),
        ],
        "agent-c": [
            ("list_files", {"path": "src/coordination_demo/policies", "max_entries": 20}),
            ("search_code", {"pattern": "SharedMemoryPolicy|plan_semantic_duplicates", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/policies/shared_memory.py", "start_line": 1, "end_line": 120}),
            ("search_code", {"pattern": "duplicate_tool_calls|duplicate_messages", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/eval/runner.py", "start_line": 320, "end_line": 420}),
        ],
        "agent-d": [
            ("list_files", {"path": "src/coordination_demo/core", "max_entries": 30}),
            ("search_code", {"pattern": "duplicate_tool_calls|semantic_duplicate_work_count", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/eval/runner.py", "start_line": 340, "end_line": 440}),
            ("search_code", {"pattern": "EventRecorder|replay_event_distribution", "scope": "src tests", "max_hits": 20}),
            ("read_file", {"path": "src/coordination_demo/core/replay.py", "start_line": 1, "end_line": 140}),
        ],
    },
}


def run_shell(command: str) -> str:
    result = subprocess.run([
        "/bin/bash",
        "-lc",
        command,
    ], check=True, capture_output=True, text=True)
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


def make_repo_tools(exec_logger: ExecLogger, target_repo: Path) -> Tuple[Callable[..., str], Callable[..., str], Callable[..., str]]:
    def list_files(path: str, max_entries: int = 40) -> str:
        started = time.monotonic()
        cmd = (
            f"cd {json.dumps(str(target_repo))} && "
            f"find {json.dumps(path)} -type f | sed 's#^./##' | sort | head -n {int(max_entries)}"
        )
        out = run_shell(cmd)
        exec_logger.log("list_files", {"path": path, "max_entries": max_entries}, cmd, out, (time.monotonic() - started) * 1000)
        return out

    def search_code(pattern: str, scope: str = "src tests", max_hits: int = 20) -> str:
        started = time.monotonic()
        cmd = (
            f"cd {json.dumps(str(target_repo))} && "
            f"grep -RIn --binary-files=without-match -E {json.dumps(pattern)} {scope} | head -n {int(max_hits)}"
        )
        out = run_shell(cmd)
        exec_logger.log("search_code", {"pattern": pattern, "scope": scope, "max_hits": max_hits}, cmd, out, (time.monotonic() - started) * 1000)
        return out

    def read_file(path: str, start_line: int = 1, end_line: int = 120) -> str:
        started = time.monotonic()
        cmd = f"cd {json.dumps(str(target_repo))} && sed -n '{int(start_line)},{int(end_line)}p' {json.dumps(path)}"
        out = run_shell(cmd)
        exec_logger.log("read_file", {"path": path, "start_line": start_line, "end_line": end_line}, cmd, out, (time.monotonic() - started) * 1000)
        return out

    return list_files, search_code, read_file


def summarize_per_tool(observed_calls: List[Dict[str, Any]], underlying_exec_log: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    observed = Counter(call["tool_name"] for call in observed_calls)
    underlying = Counter(call["tool_name"] for call in underlying_exec_log)
    all_tools = sorted(set(observed) | set(underlying))
    summary: Dict[str, Dict[str, Any]] = {}
    for tool_name in all_tools:
        observed_count = observed.get(tool_name, 0)
        underlying_count = underlying.get(tool_name, 0)
        saved = observed_count - underlying_count
        summary[tool_name] = {
            "observed_calls": observed_count,
            "underlying_executions": underlying_count,
            "dedup_saves": saved,
            "dedup_rate": round((saved / observed_count) if observed_count else 0.0, 6),
        }
    return summary


def aggregate_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    wall_clock_ms = [run["wall_clock_ms"] for run in runs]
    observed_calls = [run["observed_tool_calls"] for run in runs]
    underlying_execs = [run["underlying_executions"] for run in runs]

    per_tool_rollup: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        for tool_name, tool_summary in run["per_tool_summary"].items():
            for key in ("observed_calls", "underlying_executions", "dedup_saves", "dedup_rate"):
                per_tool_rollup[tool_name][key].append(tool_summary[key])

    per_tool = {}
    for tool_name, stats in sorted(per_tool_rollup.items()):
        per_tool[tool_name] = {
            key: round(mean(values), 6) for key, values in stats.items()
        }

    aggregate = {
        "run_count": len(runs),
        "observed_tool_calls_mean": round(mean(observed_calls), 6),
        "underlying_executions_mean": round(mean(underlying_execs), 6),
        "wall_clock_ms_mean": round(mean(wall_clock_ms), 6),
        "wall_clock_ms_min": round(min(wall_clock_ms), 6),
        "wall_clock_ms_max": round(max(wall_clock_ms), 6),
        "per_tool_mean": per_tool,
    }

    if "summary" in runs[0]:
        aggregate["dedup_rate_mean"] = round(mean(run["summary"]["dedup_rate"] for run in runs), 6)
        aggregate["cache_hit_rate_mean"] = round(mean(run["summary"]["cache_hit_rate"] for run in runs), 6)
        aggregate["calls_saved_mean"] = round(mean(run["summary"]["calls_saved"] for run in runs), 6)

    return aggregate


def run_scenario_baseline(
    scenario_name: str,
    plan: Dict[str, List[Tuple[str, Dict[str, Any]]]],
    target_repo: Path,
) -> Dict[str, Any]:
    exec_logger = ExecLogger()
    list_files, search_code, read_file = make_repo_tools(exec_logger, target_repo)
    tool_map = {"list_files": list_files, "search_code": search_code, "read_file": read_file}

    observed_calls = []
    started = time.monotonic()
    for agent_id, steps in plan.items():
        for tool_name, kwargs in steps:
            out = tool_map[tool_name](**kwargs)
            observed_calls.append({
                "agent_id": agent_id,
                "tool_name": tool_name,
                "args": kwargs,
                "output_preview": out[:200],
            })
    wall_clock_ms = (time.monotonic() - started) * 1000.0

    return {
        "mode": "baseline",
        "scenario": scenario_name,
        "observed_tool_calls": len(observed_calls),
        "underlying_executions": len(exec_logger.rows),
        "wall_clock_ms": round(wall_clock_ms, 3),
        "observed_calls": observed_calls,
        "underlying_exec_log": exec_logger.rows,
        "per_tool_summary": summarize_per_tool(observed_calls, exec_logger.rows),
    }


def run_scenario_agentglue(
    scenario_name: str,
    plan: Dict[str, List[Tuple[str, Dict[str, Any]]]],
    ttl: float,
    target_repo: Path,
) -> Dict[str, Any]:
    exec_logger = ExecLogger()
    base_list_files, base_search_code, base_read_file = make_repo_tools(exec_logger, target_repo)

    glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False, dedup_ttl=ttl)
    tool_map = {
        "list_files": glue.tool(ttl=ttl)(base_list_files),
        "search_code": glue.tool(ttl=ttl)(base_search_code),
        "read_file": glue.tool(ttl=ttl)(base_read_file),
    }

    observed_calls = []
    started = time.monotonic()
    for agent_id, steps in plan.items():
        for tool_name, kwargs in steps:
            out = tool_map[tool_name](agent_id=agent_id, **kwargs)
            observed_calls.append({
                "agent_id": agent_id,
                "tool_name": tool_name,
                "args": kwargs,
                "output_preview": out[:200],
            })
    wall_clock_ms = (time.monotonic() - started) * 1000.0

    events = glue.recorder.events if glue.recorder else []
    return {
        "mode": "agentglue",
        "scenario": scenario_name,
        "summary": {**glue.summary(), "wall_clock_ms": round(wall_clock_ms, 3)},
        "report": glue.report(),
        "observed_tool_calls": len(observed_calls),
        "underlying_executions": len(exec_logger.rows),
        "wall_clock_ms": round(wall_clock_ms, 3),
        "observed_calls": observed_calls,
        "underlying_exec_log": exec_logger.rows,
        "events": events,
        "duplicate_analysis": detect_duplicates(events),
        "per_tool_summary": summarize_per_tool(observed_calls, exec_logger.rows),
    }


def run_concurrent_probe() -> Dict[str, Any]:
    glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False, dedup_ttl=60.0)
    call_count = 0
    call_lock = threading.Lock()
    entered = threading.Event()

    @glue.tool(ttl=60.0)
    def slow_lookup(x: str) -> str:
        nonlocal call_count
        entered.set()
        time.sleep(0.1)
        with call_lock:
            call_count += 1
            current = call_count
        return f"value-{x}-{current}"

    results: Dict[str, str] = {}

    def invoke(agent_id: str) -> None:
        results[agent_id] = slow_lookup("same", agent_id=agent_id)

    started = time.monotonic()
    t1 = threading.Thread(target=invoke, args=("agent-a",))
    t1.start()
    entered.wait(timeout=2.0)
    t2 = threading.Thread(target=invoke, args=("agent-b",))
    t2.start()
    t1.join()
    t2.join()
    post_result = slow_lookup("same", agent_id="agent-c")
    wall_clock_ms = (time.monotonic() - started) * 1000.0

    events = glue.recorder.events if glue.recorder else []
    coalesced = glue.metrics.tool_calls_coalesced
    return {
        "mode": "agentglue",
        "scenario": "concurrent_probe",
        "wall_clock_ms": round(wall_clock_ms, 3),
        "results": results,
        "post_result": post_result,
        "underlying_call_count": call_count,
        "coalesced_calls": coalesced,
        "summary": {**glue.summary(), "wall_clock_ms": round(wall_clock_ms, 3)},
        "events": events,
        "duplicate_analysis": detect_duplicates(events),
        "finding": (
            "Single-flight coalescing active: concurrent identical calls share the first execution's result. "
            f"Underlying executions: {call_count}, coalesced waiters: {coalesced}."
        ),
    }


def run_scenario_harness(scenario_name: str, runs: int, ttl: float, target_repo: Path) -> Dict[str, Any]:
    plan = SCENARIO_PLANS[scenario_name]
    baseline_runs = [run_scenario_baseline(scenario_name, plan, target_repo=target_repo) for _ in range(runs)]
    glue_runs = [run_scenario_agentglue(scenario_name, plan, ttl=ttl, target_repo=target_repo) for _ in range(runs)]
    return {
        "scenario": scenario_name,
        "plan_summary": {
            "agent_count": len(plan),
            "steps_per_agent": {agent_id: len(steps) for agent_id, steps in plan.items()},
            "observed_calls_per_run": sum(len(steps) for steps in plan.values()),
        },
        "runs": {
            "baseline": baseline_runs,
            "agentglue": glue_runs,
        },
        "aggregate": {
            "baseline": aggregate_runs(baseline_runs),
            "agentglue": aggregate_runs(glue_runs),
        },
    }


def scenario_takeaway(name: str, baseline: Dict[str, Any], glue: Dict[str, Any]) -> str:
    saved = glue.get("calls_saved_mean", 0)
    dedup_rate = glue.get("dedup_rate_mean", 0.0)
    if name == "repo_exploration":
        return (
            f"Clean overlap case: AgentGlue saves {saved:.1f} executions on average "
            f"({dedup_rate:.1%} dedup rate) on repeated repo search/read/list calls."
        )
    return (
        f"Messier partial-overlap case: AgentGlue still saves {saved:.1f} executions on average "
        f"({dedup_rate:.1%} dedup rate), but exact-match scope leaves near-miss queries and different line ranges untouched."
    )


def write_markdown_summary(path: Path, scenario_results: Dict[str, Any], concurrent_probe: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    lines = [
        "# AgentGlue Benchmark Summary",
        "",
        f"- label: `{metadata['label']}`",
        f"- target_repo: `{metadata['target_repo']}`",
        f"- scenarios: **{', '.join(metadata['scenarios'])}**",
        f"- runs: **{metadata['runs']}**",
        f"- dedup_ttl_s: **{metadata['dedup_ttl_s']}**",
        "",
        "## Scenario aggregates",
        "",
    ]

    for scenario_name in metadata["scenarios"]:
        scenario = scenario_results[scenario_name]
        baseline = scenario["aggregate"]["baseline"]
        glue = scenario["aggregate"]["agentglue"]
        lines.extend([
            f"### {scenario_name}",
            "",
            f"- observed calls / run: **{scenario['plan_summary']['observed_calls_per_run']}**",
            f"- baseline underlying executions mean: **{baseline['underlying_executions_mean']}**",
            f"- agentglue underlying executions mean: **{glue['underlying_executions_mean']}**",
            f"- agentglue calls saved mean: **{glue['calls_saved_mean']}**",
            f"- agentglue dedup rate mean: **{glue['dedup_rate_mean']}**",
            f"- baseline wall clock mean: **{baseline['wall_clock_ms_mean']} ms**",
            f"- agentglue wall clock mean: **{glue['wall_clock_ms_mean']} ms**",
            f"- takeaway: {scenario_takeaway(scenario_name, baseline, glue)}",
            "",
            "Per-tool mean summary:",
        ])
        for tool_name, stats in glue["per_tool_mean"].items():
            lines.append(
                f"- `{tool_name}`: observed={stats['observed_calls']}, underlying={stats['underlying_executions']}, saves={stats['dedup_saves']}, dedup_rate={stats['dedup_rate']}"
            )
        lines.append("")

    lines.extend([
        "## Concurrent probe",
        "",
        f"- underlying_call_count: **{concurrent_probe['underlying_call_count']}**",
        f"- coalesced_calls: **{concurrent_probe.get('coalesced_calls', 'N/A')}**",
        f"- deduped_calls_in_metrics: **{concurrent_probe['summary']['tool_calls_deduped']}**",
        f"- finding: {concurrent_probe['finding']}",
        "",
        "## Interpretation",
        "",
        "AgentGlue is strongest when multiple agents make truly identical calls close together. Sequential exact matches are handled by the TTL cache; concurrent exact matches are handled by single-flight coalescing. Partial-overlap scenarios remain useful because they show the ceiling of exact-match dedup without pretending semantic dedup already exists.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dedup-ttl", type=float, default=600.0)
    parser.add_argument("--label", default="repo_exploration")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument(
        "--target-repo",
        default=str(DEFAULT_TARGET_REPO),
        help="Repository to benchmark against. Defaults to AgentGlue's self-contained benchmark fixture.",
    )
    parser.add_argument(
        "--scenario",
        dest="scenarios",
        action="append",
        choices=sorted(SCENARIO_PLANS),
        help="Benchmark scenario to run. Repeat to run multiple scenarios. Defaults to all built-in scenarios.",
    )
    args = parser.parse_args()

    scenarios = args.scenarios or list(DEFAULT_SCENARIOS)
    target_repo = Path(args.target_repo).resolve()
    if not target_repo.exists():
        raise FileNotFoundError(f"target repo does not exist: {target_repo}")
    artifact_dir = Path(args.artifact_root) / args.label
    artifact_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "label": args.label,
        "target_repo": str(target_repo),
        "scenarios": scenarios,
        "runs": args.runs,
        "dedup_ttl_s": args.dedup_ttl,
        "generated_at_epoch_s": round(time.time(), 3),
    }

    scenario_results = {
        scenario_name: run_scenario_harness(scenario_name, runs=args.runs, ttl=args.dedup_ttl, target_repo=target_repo)
        for scenario_name in scenarios
    }
    concurrent_probe = run_concurrent_probe()
    result = {
        "metadata": metadata,
        "scenarios": scenario_results,
        "concurrent_probe": concurrent_probe,
    }

    result_path = artifact_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    for scenario_name in scenarios:
        last_events = scenario_results[scenario_name]["runs"]["agentglue"][-1]["events"]
        events_path = artifact_dir / f"{scenario_name}_last_run.events.jsonl"
        events_path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in last_events), encoding="utf-8")

    concurrent_events_path = artifact_dir / "concurrent_probe.events.jsonl"
    concurrent_events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in concurrent_probe["events"]),
        encoding="utf-8",
    )

    summary_path = artifact_dir / "SUMMARY.md"
    write_markdown_summary(summary_path, scenario_results, concurrent_probe, metadata)

    print(json.dumps({
        "artifact_dir": str(artifact_dir),
        "result_json": str(result_path),
        "summary_md": str(summary_path),
        "scenario_event_logs": {
            scenario_name: str(artifact_dir / f"{scenario_name}_last_run.events.jsonl") for scenario_name in scenarios
        },
        "concurrent_events_jsonl": str(concurrent_events_path),
        "scenario_calls_saved_mean": {
            scenario_name: scenario_results[scenario_name]["aggregate"]["agentglue"].get("calls_saved_mean")
            for scenario_name in scenarios
        },
        "concurrent_underlying_call_count": concurrent_probe["underlying_call_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
