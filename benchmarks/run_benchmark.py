#!/usr/bin/env python3
"""
AgentGlue End-to-End Benchmark Runner

Two benchmark suites:
  --suite e2e      30 multi-agent tasks (spawn 2-6 sub-agents each)
  --suite simple   100 lightweight multi-agent tasks (2-3 agents each)

Three run modes:
  --mode baseline    Run WITHOUT AgentGlue plugin (plugin must not be active)
  --mode agentglue   Run WITH AgentGlue plugin (plugin must be installed)
  --mode compare     Automatic A/B: disable plugin → run baseline → enable → run agentglue → report

Usage:
  # One-command A/B comparison (recommended)
  python3 benchmarks/run_benchmark.py --suite simple --mode compare
  python3 benchmarks/run_benchmark.py --suite e2e --mode compare

  # Or run each phase manually
  python3 benchmarks/run_benchmark.py --suite e2e --mode baseline
  python3 benchmarks/run_benchmark.py --suite e2e --mode agentglue

  # Run specific tasks only
  python3 benchmarks/run_benchmark.py --suite e2e --mode compare --tasks T01,T04,T07

  # Compare two existing result files
  python3 benchmarks/run_benchmark.py --compare results/e2e_baseline_*.json results/e2e_agentglue_*.json

  # Dry run — show tasks without executing
  python3 benchmarks/run_benchmark.py --suite e2e --mode baseline --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
E2E_TASKS_FILE = SCRIPT_DIR / "tasks" / "e2e.json"
SIMPLE_TASKS_FILE = SCRIPT_DIR / "tasks" / "simple.json"
RESULTS_DIR = SCRIPT_DIR / "results"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_openclaw(args: list[str], timeout: int = 900) -> dict:
    """Run an openclaw CLI command and return parsed JSON or raw output."""
    cmd = ["openclaw"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Try JSON parse first
        try:
            return {"ok": True, "data": json.loads(result.stdout), "stderr": result.stderr}
        except json.JSONDecodeError:
            return {"ok": result.returncode == 0, "data": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "data": None, "stderr": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "data": None, "stderr": str(e)}


def check_gateway() -> bool:
    """Check if OpenClaw gateway is running."""
    r = run_openclaw(["gateway", "call", "health"], timeout=10)
    return r["ok"]


def check_agentglue_plugin() -> bool:
    """Check if AgentGlue plugin is loaded."""
    r = run_openclaw(["plugins", "list", "--json"], timeout=15)
    if r["ok"] and isinstance(r["data"], (list, dict)):
        data = r["data"]
        if isinstance(data, list):
            return any("agentglue" in str(p).lower() for p in data)
        return "agentglue" in str(data).lower()
    # Fallback: check raw output
    if isinstance(r["data"], str):
        return "agentglue" in r["data"].lower()
    return False


OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"


def _read_openclaw_config() -> dict:
    """Read the OpenClaw config file."""
    with open(OPENCLAW_CONFIG) as f:
        return json.load(f)


def _write_openclaw_config(config: dict) -> None:
    """Write the OpenClaw config file (with backup)."""
    backup = OPENCLAW_CONFIG.with_suffix(".json.bench-bak")
    if OPENCLAW_CONFIG.exists():
        import shutil
        shutil.copy2(OPENCLAW_CONFIG, backup)
    with open(OPENCLAW_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def set_agentglue_enabled(enabled: bool) -> bool:
    """Enable or disable the AgentGlue plugin in openclaw.json.

    Returns True if a change was made.
    """
    config = _read_openclaw_config()
    plugins = config.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    ag = entries.setdefault("openclaw-agentglue", {})

    current = ag.get("enabled", True)  # default is enabled if entry exists
    if current == enabled:
        return False

    ag["enabled"] = enabled
    _write_openclaw_config(config)
    return True


def is_agentglue_installed() -> bool:
    """Check if the AgentGlue plugin is installed (regardless of enabled state)."""
    try:
        config = _read_openclaw_config()
        installs = config.get("plugins", {}).get("installs", {})
        if "openclaw-agentglue" in installs:
            return True
        # Also check extensions dir
        ext_dir = Path.home() / ".openclaw" / "extensions" / "openclaw-agentglue"
        return ext_dir.exists()
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def restart_gateway(wait: int = 8) -> bool:
    """Restart the OpenClaw gateway and wait for it to be healthy."""
    print(f"  Restarting gateway...", end="", flush=True)
    subprocess.run(["systemctl", "--user", "restart", "openclaw-gateway"],
                   capture_output=True, timeout=15)
    # Wait for health
    for i in range(wait):
        time.sleep(1)
        print(".", end="", flush=True)
        if check_gateway():
            print(" OK")
            return True
    print(" TIMEOUT")
    return False


def get_agentglue_metrics() -> dict | None:
    """Fetch AgentGlue cache metrics if available."""
    r = run_openclaw(
        ["agent", "--message", "Call the agentglue_metrics tool and return its raw JSON output. Nothing else.", "--json", "--timeout", "30"],
        timeout=60,
    )
    if r["ok"] and isinstance(r["data"], dict):
        # Try to extract metrics from agent response
        reply = r["data"].get("reply", "") or r["data"].get("message", "") or str(r["data"])
        try:
            # Find JSON in the reply
            start = reply.find("{")
            end = reply.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(reply[start:end])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def fetch_session_history(session_id: str) -> list[dict]:
    """Fetch full session transcript including tool calls."""
    r = run_openclaw(
        ["agent", "--session-id", session_id,
         "--message", "Call the sessions_history tool for this session with includeTools=true and limit=500. Return the raw JSON.",
         "--json", "--timeout", "60"],
        timeout=120,
    )
    # Also try reading transcript file directly
    transcript_dir = Path.home() / ".openclaw" / "agents" / "default" / "sessions"
    transcript_file = transcript_dir / f"{session_id}.jsonl"
    messages = []
    if transcript_file.exists():
        with open(transcript_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return messages


def parse_transcript(messages: list[dict]) -> dict:
    """Extract metrics from a session transcript."""
    tool_calls = []
    total_input_tokens = 0
    total_output_tokens = 0
    sub_sessions = []

    for msg in messages:
        msg_type = msg.get("type", "")

        # Count tokens from usage
        usage = msg.get("usage", {})
        if usage:
            total_input_tokens += usage.get("inputTokens", 0) or usage.get("input_tokens", 0) or 0
            total_output_tokens += usage.get("outputTokens", 0) or usage.get("output_tokens", 0) or 0

        # Extract tool calls
        if msg_type == "toolResult" or msg.get("role") == "toolResult":
            tool_name = msg.get("toolName", "") or msg.get("name", "")
            if tool_name:
                is_cache_hit = False
                result_text = str(msg.get("result", "") or msg.get("content", ""))
                if "[cache hit" in result_text:
                    is_cache_hit = True
                tool_calls.append({
                    "tool": tool_name,
                    "cache_hit": is_cache_hit,
                    "timestamp": msg.get("timestamp", 0),
                })

        # Detect sub-agent spawns
        content = msg.get("message", "") or msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    if tool_name:
                        tool_calls.append({
                            "tool": tool_name,
                            "cache_hit": False,
                            "timestamp": msg.get("timestamp", 0),
                        })
                    if tool_name == "sessions_spawn":
                        inp = block.get("input", {})
                        sub_sessions.append({
                            "task": inp.get("task", "")[:100],
                            "label": inp.get("label", ""),
                        })

    # Aggregate by tool type
    tool_counts = {}
    cache_hits = 0
    cache_checks = 0
    for tc in tool_calls:
        t = tc["tool"]
        tool_counts[t] = tool_counts.get(t, 0) + 1
        if "agentglue" in t.lower() or "cached" in t.lower():
            cache_checks += 1
            if tc["cache_hit"]:
                cache_hits += 1

    return {
        "total_tool_calls": len(tool_calls),
        "tool_counts": tool_counts,
        "cache_hits": cache_hits,
        "cache_checks": cache_checks,
        "sub_agents_spawned": len(sub_sessions),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
    }


def collect_sub_session_transcripts(session_id: str) -> list[dict]:
    """Try to collect transcripts from sub-agent sessions."""
    sessions_dir = Path.home() / ".openclaw" / "agents" / "default" / "sessions"
    all_transcripts = []

    # Read sessions.json to find child sessions
    sessions_meta = sessions_dir / "sessions.json"
    if sessions_meta.exists():
        try:
            with open(sessions_meta) as f:
                meta = json.load(f)
            # Look for subagent sessions spawned after our session started
            for key, entry in meta.items() if isinstance(meta, dict) else []:
                if "subagent" in key and session_id[:8] in str(entry):
                    sid = entry.get("sessionId", "")
                    if sid:
                        transcript_file = sessions_dir / f"{sid}.jsonl"
                        if transcript_file.exists():
                            messages = []
                            with open(transcript_file) as f2:
                                for line in f2:
                                    line = line.strip()
                                    if line:
                                        try:
                                            messages.append(json.loads(line))
                                        except json.JSONDecodeError:
                                            continue
                            all_transcripts.append({
                                "session_key": key,
                                "messages": messages,
                            })
        except (json.JSONDecodeError, KeyError):
            pass

    return all_transcripts


# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

def run_task(task: dict, mode: str, model: str | None, timeout: int, suite: str = "e2e") -> dict:
    """Run a single benchmark task and collect metrics."""
    task_id = task["id"]
    session_id = f"bench-{task_id}-{mode}-{uuid.uuid4().hex[:8]}"
    num_agents = task.get("num_agents", 1)
    expected_overlap = task.get("expected_overlap", "n/a")

    print(f"\n{'='*60}")
    print(f"  Task: {task_id} — {task['name']}")
    if suite == "e2e":
        print(f"  Mode: {mode} | Agents: {num_agents} | Overlap: {expected_overlap}")
    else:
        print(f"  Mode: {mode} | Category: {task.get('category', '?')}")
    print(f"  Session: {session_id}")
    print(f"{'='*60}")

    # Build agent command
    agent_args = [
        "agent",
        "--session-id", session_id,
        "--message", task["prompt"],
        "--json",
        "--timeout", str(timeout),
    ]
    if model:
        # Inject model hint into the message (openclaw agent doesn't have --model for gateway mode)
        pass  # model is set via config; we can override via thinking level

    # Capture AgentGlue metrics before (if in agentglue mode)
    metrics_before = None
    if mode == "agentglue":
        metrics_before = get_agentglue_metrics()

    # Run the task
    start_time = time.time()
    result = run_openclaw(agent_args, timeout=timeout)
    wall_time = time.time() - start_time

    print(f"  Completed in {wall_time:.1f}s (ok={result['ok']})")

    # Capture AgentGlue metrics after
    metrics_after = None
    if mode == "agentglue":
        metrics_after = get_agentglue_metrics()

    # Parse main session transcript
    main_messages = fetch_session_history(session_id)
    main_metrics = parse_transcript(main_messages)

    # Try to collect sub-session transcripts
    sub_transcripts = collect_sub_session_transcripts(session_id)
    sub_metrics = []
    for st in sub_transcripts:
        sm = parse_transcript(st["messages"])
        sm["session_key"] = st["session_key"]
        sub_metrics.append(sm)

    # Aggregate all metrics
    total_tool_calls = main_metrics["total_tool_calls"] + sum(s["total_tool_calls"] for s in sub_metrics)
    total_tokens = main_metrics["total_tokens"] + sum(s["total_tokens"] for s in sub_metrics)
    total_cache_hits = main_metrics["cache_hits"] + sum(s["cache_hits"] for s in sub_metrics)
    total_cache_checks = main_metrics["cache_checks"] + sum(s["cache_checks"] for s in sub_metrics)

    # Merge tool counts
    merged_tool_counts = dict(main_metrics["tool_counts"])
    for sm in sub_metrics:
        for t, c in sm["tool_counts"].items():
            merged_tool_counts[t] = merged_tool_counts.get(t, 0) + c

    # Compute delta from AgentGlue metrics
    agentglue_delta = {}
    if metrics_before and metrics_after:
        for key in metrics_after:
            if isinstance(metrics_after[key], (int, float)) and key in metrics_before:
                agentglue_delta[key] = metrics_after[key] - metrics_before.get(key, 0)

    task_result = {
        "task_id": task_id,
        "task_name": task["name"],
        "category": task.get("category", ""),
        "num_agents": num_agents,
        "expected_overlap": expected_overlap,
        "mode": mode,
        "session_id": session_id,
        "success": result["ok"],
        "wall_time_s": round(wall_time, 2),
        "total_tool_calls": total_tool_calls,
        "tool_counts": merged_tool_counts,
        "cache_hits": total_cache_hits,
        "cache_checks": total_cache_checks,
        "cache_hit_rate": round(total_cache_hits / max(total_cache_checks, 1), 3),
        "tokens": {
            "input": main_metrics["input_tokens"] + sum(s["input_tokens"] for s in sub_metrics),
            "output": main_metrics["output_tokens"] + sum(s["output_tokens"] for s in sub_metrics),
            "total": total_tokens,
        },
        "sub_agents_spawned": main_metrics["sub_agents_spawned"],
        "sub_session_count": len(sub_metrics),
        "agentglue_metrics_delta": agentglue_delta,
        "errors": result["stderr"] if not result["ok"] else "",
    }

    # Print summary
    print(f"  Tool calls: {total_tool_calls} | Tokens: {total_tokens}")
    print(f"  Sub-agents spawned: {main_metrics['sub_agents_spawned']} (transcripts found: {len(sub_metrics)})")
    if mode == "agentglue":
        print(f"  Cache hits: {total_cache_hits}/{total_cache_checks} ({task_result['cache_hit_rate']:.0%})")

    return task_result


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def load_results(path: str) -> dict:
    """Load a results JSON file."""
    with open(path) as f:
        return json.load(f)


def compare_results(baseline_path: str, agentglue_path: str):
    """Generate a comparison report from two result files."""
    baseline = load_results(baseline_path)
    agentglue = load_results(agentglue_path)

    bl_tasks = {r["task_id"]: r for r in baseline["tasks"]}
    ag_tasks = {r["task_id"]: r for r in agentglue["tasks"]}

    common_ids = sorted(set(bl_tasks) & set(ag_tasks))
    if not common_ids:
        print("No common tasks found between the two result files.")
        return

    print("\n" + "=" * 100)
    print("  AgentGlue End-to-End Benchmark: Baseline vs AgentGlue")
    print("=" * 100)
    print(f"\n  Baseline:   {baseline_path}")
    print(f"  AgentGlue:  {agentglue_path}")
    print(f"  Tasks compared: {len(common_ids)}")

    # Header
    print(f"\n{'Task':<35} {'Agents':>6} {'Overlap':>10} {'BL Time':>8} {'AG Time':>8} {'Speedup':>8} {'BL Tools':>8} {'AG Tools':>8} {'Hits':>6} {'HitRate':>8}")
    print("-" * 115)

    total_bl_time = 0
    total_ag_time = 0
    total_bl_tools = 0
    total_ag_tools = 0
    total_hits = 0
    total_checks = 0
    total_bl_tokens = 0
    total_ag_tokens = 0

    for tid in common_ids:
        bl = bl_tasks[tid]
        ag = ag_tasks[tid]

        bl_time = bl["wall_time_s"]
        ag_time = ag["wall_time_s"]
        speedup = bl_time / ag_time if ag_time > 0 else float("inf")

        total_bl_time += bl_time
        total_ag_time += ag_time
        total_bl_tools += bl["total_tool_calls"]
        total_ag_tools += ag["total_tool_calls"]
        total_hits += ag["cache_hits"]
        total_checks += ag["cache_checks"]
        total_bl_tokens += bl["tokens"]["total"]
        total_ag_tokens += ag["tokens"]["total"]

        hit_rate = f"{ag['cache_hit_rate']:.0%}" if ag["cache_checks"] > 0 else "n/a"

        print(
            f"{tid:<35} {bl['num_agents']:>6} {bl['expected_overlap']:>10} "
            f"{bl_time:>7.1f}s {ag_time:>7.1f}s {speedup:>7.2f}x "
            f"{bl['total_tool_calls']:>8} {ag['total_tool_calls']:>8} "
            f"{ag['cache_hits']:>6} {hit_rate:>8}"
        )

    # Totals
    print("-" * 115)
    overall_speedup = total_bl_time / total_ag_time if total_ag_time > 0 else float("inf")
    overall_hit_rate = total_hits / max(total_checks, 1)
    time_saved = (total_bl_time - total_ag_time) / total_bl_time * 100 if total_bl_time > 0 else 0
    token_saved = (total_bl_tokens - total_ag_tokens) / total_bl_tokens * 100 if total_bl_tokens > 0 else 0

    print(
        f"{'TOTAL':<35} {'':>6} {'':>10} "
        f"{total_bl_time:>7.1f}s {total_ag_time:>7.1f}s {overall_speedup:>7.2f}x "
        f"{total_bl_tools:>8} {total_ag_tools:>8} "
        f"{total_hits:>6} {overall_hit_rate:>7.0%}"
    )

    print(f"\n  Summary:")
    print(f"    Overall speedup:      {overall_speedup:.2f}x")
    print(f"    Time saved:           {time_saved:.1f}%")
    print(f"    Token saved:          {token_saved:.1f}%")
    print(f"    Cache hit rate:       {overall_hit_rate:.1%}")
    print(f"    Total cache hits:     {total_hits}/{total_checks}")
    print(f"    Baseline tokens:      {total_bl_tokens:,}")
    print(f"    AgentGlue tokens:     {total_ag_tokens:,}")

    # Save comparison report
    report = {
        "generated_at": datetime.now().isoformat(),
        "baseline_file": baseline_path,
        "agentglue_file": agentglue_path,
        "tasks_compared": len(common_ids),
        "summary": {
            "overall_speedup": round(overall_speedup, 2),
            "time_saved_pct": round(time_saved, 1),
            "token_saved_pct": round(token_saved, 1),
            "cache_hit_rate": round(overall_hit_rate, 3),
            "total_cache_hits": total_hits,
            "total_cache_checks": total_checks,
            "baseline_total_time_s": round(total_bl_time, 1),
            "agentglue_total_time_s": round(total_ag_time, 1),
            "baseline_total_tokens": total_bl_tokens,
            "agentglue_total_tokens": total_ag_tokens,
        },
        "per_task": [],
    }

    for tid in common_ids:
        bl = bl_tasks[tid]
        ag = ag_tasks[tid]
        speedup = bl["wall_time_s"] / ag["wall_time_s"] if ag["wall_time_s"] > 0 else 0
        report["per_task"].append({
            "task_id": tid,
            "task_name": bl["task_name"],
            "num_agents": bl["num_agents"],
            "expected_overlap": bl["expected_overlap"],
            "baseline_time_s": bl["wall_time_s"],
            "agentglue_time_s": ag["wall_time_s"],
            "speedup": round(speedup, 2),
            "baseline_tool_calls": bl["total_tool_calls"],
            "agentglue_tool_calls": ag["total_tool_calls"],
            "cache_hits": ag["cache_hits"],
            "cache_hit_rate": ag["cache_hit_rate"],
            "baseline_tokens": bl["tokens"]["total"],
            "agentglue_tokens": ag["tokens"]["total"],
        })

    report_path = RESULTS_DIR / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {report_path}")


# ---------------------------------------------------------------------------
# Run a single phase (baseline or agentglue) and return results + output path
# ---------------------------------------------------------------------------

def run_phase(mode: str, suite: str, tasks: list[dict], model: str | None,
              timeout: int) -> tuple[dict, Path]:
    """Run one benchmark phase and save results. Returns (output_dict, output_path)."""
    suite_label = "E2E Multi-Agent" if suite == "e2e" else "Simple (multi-agent)"

    plugin_active = check_agentglue_plugin()

    RESULTS_DIR.mkdir(exist_ok=True)
    results = []
    start_all = time.time()

    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}]", end="")
        try:
            result = run_task(task, mode, model, timeout, suite=suite)
            results.append(result)
        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Saving partial results...")
            break
        except Exception as e:
            print(f"  [ERROR] {e}")
            results.append({
                "task_id": task["id"],
                "task_name": task["name"],
                "mode": mode,
                "success": False,
                "wall_time_s": 0,
                "total_tool_calls": 0,
                "errors": str(e),
            })

    total_time = time.time() - start_all

    output = {
        "generated_at": datetime.now().isoformat(),
        "suite": suite,
        "mode": mode,
        "model": model or "default",
        "total_tasks": len(results),
        "total_time_s": round(total_time, 1),
        "plugin_active": plugin_active,
        "tasks": results,
        "summary": {
            "successful": sum(1 for r in results if r.get("success")),
            "failed": sum(1 for r in results if not r.get("success")),
            "total_wall_time_s": round(sum(r.get("wall_time_s", 0) for r in results), 1),
            "total_tool_calls": sum(r.get("total_tool_calls", 0) for r in results),
            "total_tokens": sum(r.get("tokens", {}).get("total", 0) for r in results),
            "total_cache_hits": sum(r.get("cache_hits", 0) for r in results),
            "total_cache_checks": sum(r.get("cache_checks", 0) for r in results),
        },
    }

    filename = f"{suite}_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path = RESULTS_DIR / filename
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print phase summary
    s = output["summary"]
    print(f"\n{'='*60}")
    print(f"  Phase Complete — {suite_label} / {mode}")
    print(f"{'='*60}")
    print(f"  Tasks:      {s['successful']} passed / {s['failed']} failed")
    print(f"  Wall time:  {s['total_wall_time_s']:.1f}s")
    print(f"  Tool calls: {s['total_tool_calls']}")
    print(f"  Tokens:     {s['total_tokens']:,}")
    if mode == "agentglue" and s["total_cache_checks"] > 0:
        rate = s["total_cache_hits"] / s["total_cache_checks"]
        print(f"  Cache hits: {s['total_cache_hits']}/{s['total_cache_checks']} ({rate:.0%})")
    print(f"  Results saved: {output_path}")

    return output, output_path


# ---------------------------------------------------------------------------
# Compare mode: automatic A/B with plugin toggle
# ---------------------------------------------------------------------------

def run_compare_mode(args):
    """Run baseline (plugin disabled) then agentglue (plugin enabled), then compare."""
    if args.timeout is None:
        args.timeout = 600 if args.suite == "e2e" else 120

    # Load tasks
    tasks_file = E2E_TASKS_FILE if args.suite == "e2e" else SIMPLE_TASKS_FILE
    with open(tasks_file) as f:
        task_data = json.load(f)
    all_tasks = task_data["tasks"]

    if args.tasks:
        selected = set(args.tasks.split(","))
        tasks = [t for t in all_tasks if t["id"] in selected]
        if not tasks:
            print(f"No tasks matched: {args.tasks}")
            return
    else:
        tasks = all_tasks

    suite_label = "E2E Multi-Agent" if args.suite == "e2e" else "Simple (multi-agent)"
    total_agents = sum(t.get("num_agents", 1) for t in tasks)

    print(f"\n{'='*70}")
    print(f"  AgentGlue A/B Comparison — {suite_label}")
    print(f"{'='*70}")
    print(f"  Tasks: {len(tasks)} | Sub-agents: {total_agents}")
    print(f"  Timeout: {args.timeout}s per task")
    print(f"  Plan: disable plugin → run baseline → enable plugin → run agentglue → compare")

    if args.dry_run:
        print(f"\n  [DRY RUN] Would run {len(tasks)} tasks × 2 phases = {len(tasks)*2} runs")
        print(f"  Total sub-agents: {total_agents * 2}")
        return

    # Pre-flight
    print(f"\nPre-flight checks:")

    if not check_gateway():
        print("  [FAIL] Gateway not running. Start with: systemctl --user start openclaw-gateway")
        return
    print("  [OK] Gateway is running")

    if not is_agentglue_installed():
        print("  [FAIL] AgentGlue plugin is not installed.")
        print("         Install with: openclaw plugins install openclaw-agentglue")
        print("         Then run this command again.")
        return
    print("  [OK] AgentGlue plugin is installed")

    # ── Phase 1: Baseline (disable plugin) ──
    print(f"\n{'='*70}")
    print(f"  PHASE 1/2: BASELINE (AgentGlue disabled)")
    print(f"{'='*70}")

    changed = set_agentglue_enabled(False)
    if changed:
        print("  Disabled AgentGlue plugin in config")
        if not restart_gateway():
            print("  [FAIL] Gateway did not restart. Aborting.")
            set_agentglue_enabled(True)  # restore
            return
    else:
        print("  AgentGlue already disabled")
        # Still verify gateway is up without the plugin
        if check_agentglue_plugin():
            print("  [WARN] Plugin still appears active — restarting gateway")
            if not restart_gateway():
                print("  [FAIL] Gateway did not restart. Aborting.")
                return

    # Verify plugin is off
    if check_agentglue_plugin():
        print("  [WARN] AgentGlue still appears in plugin list — baseline may be tainted")

    baseline_output, baseline_path = run_phase("baseline", args.suite, tasks, args.model, args.timeout)

    # ── Phase 2: AgentGlue (enable plugin) ──
    print(f"\n{'='*70}")
    print(f"  PHASE 2/2: AGENTGLUE (enabled)")
    print(f"{'='*70}")

    changed = set_agentglue_enabled(True)
    if changed:
        print("  Enabled AgentGlue plugin in config")
        if not restart_gateway(wait=15):
            print("  [FAIL] Gateway did not restart. Aborting.")
            return
    else:
        print("  AgentGlue already enabled")

    # Wait a bit extra for sidecar startup
    if not check_agentglue_plugin():
        print("  Waiting for plugin to load...", end="", flush=True)
        for _ in range(10):
            time.sleep(1)
            print(".", end="", flush=True)
            if check_agentglue_plugin():
                break
        print()

    if check_agentglue_plugin():
        print("  [OK] AgentGlue plugin is active")
    else:
        print("  [WARN] AgentGlue plugin not detected — results may not show cache hits")

    agentglue_output, agentglue_path = run_phase("agentglue", args.suite, tasks, args.model, args.timeout)

    # ── Comparison ──
    print(f"\n{'='*70}")
    print(f"  COMPARISON REPORT")
    print(f"{'='*70}")

    compare_results(str(baseline_path), str(agentglue_path))

    # Show cumulative cache curve for simple suite
    if args.suite == "simple" and len(agentglue_output.get("tasks", [])) > 5:
        results = agentglue_output["tasks"]
        print(f"\n  Cache hit rate over time (cumulative):")
        cum_hits = 0
        cum_checks = 0
        milestones = [10, 25, 50, 75, 100]
        for i, r in enumerate(results, 1):
            cum_hits += r.get("cache_hits", 0)
            cum_checks += r.get("cache_checks", 0)
            if i in milestones or i == len(results):
                rate = cum_hits / max(cum_checks, 1)
                print(f"    After {i:>3} tasks: {rate:.0%} ({cum_hits}/{cum_checks})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AgentGlue End-to-End Multi-Agent Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--suite", choices=["e2e", "simple"], default="e2e", help="Benchmark suite: e2e (30 multi-agent) or simple (100 multi-agent)")
    parser.add_argument("--mode", choices=["baseline", "agentglue", "compare"], help="Run mode (compare = automatic A/B)")
    parser.add_argument("--tasks", type=str, default="", help="Comma-separated task IDs to run (default: all)")
    parser.add_argument("--model", type=str, default=None, help="Model override (e.g. zai/glm-5)")
    parser.add_argument("--timeout", type=int, default=None, help="Per-task timeout in seconds (default: 600 for e2e, 120 for simple)")
    parser.add_argument("--dry-run", action="store_true", help="Show tasks without executing")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "AGENTGLUE"), help="Compare two existing result files")
    args = parser.parse_args()

    # Compare existing result files
    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        return

    if not args.mode:
        parser.error("--mode is required (unless using --compare)")

    # Handle --mode compare: run both phases automatically
    if args.mode == "compare":
        run_compare_mode(args)
        return

    # Set default timeout based on suite
    if args.timeout is None:
        args.timeout = 600 if args.suite == "e2e" else 120

    # Load tasks
    tasks_file = E2E_TASKS_FILE if args.suite == "e2e" else SIMPLE_TASKS_FILE
    with open(tasks_file) as f:
        task_data = json.load(f)
    all_tasks = task_data["tasks"]

    # Filter tasks
    if args.tasks:
        selected = set(args.tasks.split(","))
        tasks = [t for t in all_tasks if t["id"] in selected]
        if not tasks:
            print(f"No tasks matched: {args.tasks}")
            print(f"Available: {', '.join(t['id'] for t in all_tasks)}")
            return
    else:
        tasks = all_tasks

    suite_label = "E2E Multi-Agent" if args.suite == "e2e" else "Simple (100 tasks)"
    print(f"\nAgentGlue Benchmark — {suite_label}")
    print(f"  Mode: {args.mode}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Timeout: {args.timeout}s per task")

    if args.dry_run:
        if args.suite == "e2e":
            print(f"\n{'ID':<30} {'Name':<40} {'Agents':>6} {'Overlap':>10}")
            print("-" * 90)
            for t in tasks:
                print(f"{t['id']:<30} {t['name']:<40} {t.get('num_agents', 1):>6} {t.get('expected_overlap', ''):>10}")
            total_agents = sum(t.get("num_agents", 1) for t in tasks)
            print(f"\nTotal sub-agents to spawn: {total_agents}")
        else:
            print(f"\n{'ID':<10} {'Name':<50} {'Category':>10}")
            print("-" * 75)
            for t in tasks:
                print(f"{t['id']:<10} {t['name']:<50} {t.get('category', ''):>10}")
            print(f"\nTotal tasks: {len(tasks)}")
            # Show overlap stats
            categories = {}
            for t in tasks:
                c = t.get("category", "other")
                categories[c] = categories.get(c, 0) + 1
            print("Categories:", ", ".join(f"{k}={v}" for k, v in sorted(categories.items())))
        print(f"Estimated cost: depends on model (cheapest with zai/glm-5)")
        return

    # Pre-flight checks
    print("\nPre-flight checks:")

    if not check_gateway():
        print("  [FAIL] Gateway not running. Start with: systemctl --user start openclaw-gateway")
        return
    print("  [OK] Gateway is running")

    plugin_active = check_agentglue_plugin()
    if args.mode == "agentglue" and not plugin_active:
        print("  [FAIL] AgentGlue plugin not found. Install with: openclaw plugins install openclaw-agentglue")
        return
    if args.mode == "baseline" and plugin_active:
        print("  [WARN] AgentGlue plugin IS active — baseline results will include cache effects!")
        print("         Uninstall first, or use --mode compare for automatic A/B")
        resp = input("  Continue anyway? [y/N] ")
        if resp.lower() != "y":
            return
    status = "active" if plugin_active else "not installed"
    print(f"  [OK] AgentGlue plugin: {status}")

    # Run phase
    output, output_path = run_phase(args.mode, args.suite, tasks, args.model, args.timeout)

    # Print next step
    print(f"\n  Next step:")
    if args.mode == "baseline":
        print(f"    1. Install AgentGlue: openclaw plugins install openclaw-agentglue")
        print(f"    2. Restart gateway:   systemctl --user restart openclaw-gateway")
        print(f"    3. Run with plugin:   python3 benchmarks/run_benchmark.py --suite {args.suite} --mode agentglue")
        print(f"    4. Compare:           python3 benchmarks/run_benchmark.py --compare {output_path} results/{args.suite}_agentglue_*.json")
        print(f"\n  Or use --mode compare for automatic A/B in one command.")
    else:
        print(f"    Compare with baseline: python3 benchmarks/run_benchmark.py --compare results/{args.suite}_baseline_*.json {output_path}")


if __name__ == "__main__":
    main()
