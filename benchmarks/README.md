# AgentGlue E2E Benchmark

End-to-end multi-agent benchmark measuring real AgentGlue impact through OpenClaw.

## What it measures

Each task dispatches N sub-agents (via `sessions_spawn`) that perform overlapping work on the AgentGlue repo itself. Metrics collected:

- **Wall-clock time** — total time per task
- **Tool calls** — count by type (read, grep, glob, etc.)
- **Token usage** — input + output tokens
- **Cache hits** — AgentGlue cache hit rate (when plugin active)

## Suites

### E2E (30 tasks, 95 sub-agents)

Complex multi-agent tasks: code review, bug hunting, security audit, architecture review, etc. Each task spawns 2-6 sub-agents.

### Simple (30 tasks, 67 sub-agents)

Lightweight multi-agent tasks across 10 coordination patterns (3 tasks each):

| Group | Pattern | Tasks | Agents/task |
|---|---|---:|---:|
| 1 | Same file, different questions | 3 | 2 |
| 2 | Same search, different scopes | 3 | 2 |
| 3 | Parallel file reads | 3 | 3 |
| 4 | Cross-reference check | 3 | 2 |
| 5 | Duplicate review (security vs perf) | 3 | 2 |
| 6 | Search then read | 3 | 2 |
| 7 | Multi-file summary | 3 | 3 |
| 8 | Test vs source | 3 | 2 |
| 9 | Config + code | 3 | 2 |
| 10 | Full overlap stress | 3 | 2-3 |

## How to run

### Prerequisites

- OpenClaw gateway running: `systemctl --user start openclaw-gateway`
- AgentGlue plugin installed: `openclaw plugins install openclaw-agentglue`
- Python 3.10+

### One-command A/B comparison (recommended)

The script automatically disables the plugin for baseline, re-enables for agentglue, restarts the gateway between phases, and produces a comparison report:

```bash
cd /home/ubuntu/.openclaw/workspace/projects/AgentGlue

# Simple suite (30 tasks, ~40-90min)
python3 benchmarks/run_benchmark.py --suite simple --mode compare

# E2E suite (30 tasks, ~2-5h)
python3 benchmarks/run_benchmark.py --suite e2e --mode compare

# Test with a few tasks first
python3 benchmarks/run_benchmark.py --suite simple --mode compare --tasks S001,S002,S003
```

### Manual phases

```bash
# Baseline (plugin disabled)
python3 benchmarks/run_benchmark.py --suite simple --mode baseline

# AgentGlue (plugin enabled)
python3 benchmarks/run_benchmark.py --suite simple --mode agentglue

# Compare existing results
python3 benchmarks/run_benchmark.py --compare results/simple_baseline_*.json results/simple_agentglue_*.json
```

## Options

```bash
# Run specific tasks
python3 benchmarks/run_benchmark.py --suite e2e --mode compare --tasks T01,T04,T07

# Dry run (show plan, no execution)
python3 benchmarks/run_benchmark.py --suite simple --mode compare --dry-run

# Custom timeout per task
python3 benchmarks/run_benchmark.py --suite e2e --mode compare --timeout 300
```

## Output

Results are saved to `benchmarks/results/` as JSON:
- `{suite}_baseline_YYYYMMDD_HHMMSS.json`
- `{suite}_agentglue_YYYYMMDD_HHMMSS.json`
- `comparison_YYYYMMDD_HHMMSS.json`

## How compare mode works

```
1. Read openclaw.json → set plugins.entries.openclaw-agentglue.enabled = false
2. Restart gateway (systemctl --user restart openclaw-gateway)
3. Verify plugin is NOT active
4. Run all tasks → save baseline results
5. Set plugins.entries.openclaw-agentglue.enabled = true
6. Restart gateway
7. Verify plugin IS active (abort if not — will not run with dead plugin)
8. Smoke test: verify caching works end-to-end (abort if not)
9. Run same tasks (prompts augmented to use agentglue_cached_* tools) → save agentglue results
10. Generate comparison report
```

## Design notes

The AgentGlue plugin exposes proxy tools (`agentglue_cached_read`, `agentglue_cached_search`,
`agentglue_cached_list`) that check a shared SQLite cache before executing. Standard tools
(`read`, `grep`, `glob`) bypass the cache entirely. In agentglue mode the benchmark prepends
an instruction to each sub-agent prompt directing it to use the proxy tools, so cache hits
can actually occur. Baseline mode uses unmodified prompts with standard tools.
