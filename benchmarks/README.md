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

### Simple (100 tasks, 224 sub-agents)

Lightweight multi-agent tasks across 10 coordination patterns:

| Group | Pattern | Tasks | Agents/task |
|---|---|---:|---:|
| 1 | Same file, different questions | 10 | 2 |
| 2 | Same search, different scopes | 10 | 2 |
| 3 | Parallel file reads | 10 | 3 |
| 4 | Cross-reference check | 10 | 2 |
| 5 | Duplicate review (security vs perf) | 10 | 2 |
| 6 | Search then read | 10 | 2 |
| 7 | Multi-file summary | 10 | 3 |
| 8 | Test vs source | 10 | 2 |
| 9 | Config + code | 10 | 2 |
| 10 | Full overlap stress | 10 | 2-3 |

## How to run

### Prerequisites

- OpenClaw gateway running: `systemctl --user start openclaw-gateway`
- AgentGlue plugin installed: `openclaw plugins install openclaw-agentglue`
- Python 3.10+

### One-command A/B comparison (recommended)

The script automatically disables the plugin for baseline, re-enables for agentglue, restarts the gateway between phases, and produces a comparison report:

```bash
cd /home/ubuntu/.openclaw/workspace/projects/AgentGlue

# Simple suite (100 tasks, ~2-4h)
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

## Cost estimate

| Suite | Sub-agents | GPT-5.4 | GLM-5 | Kimi K2.5 |
|---|---:|---:|---:|---:|
| Simple (×2 phases) | 448 | ~$8-15 | ~$2-4 | ~$1-3 |
| E2E (×2 phases) | 190 | ~$5-10 | ~$1-3 | ~$0.5-2 |

## How compare mode works

```
1. Read openclaw.json → set plugins.entries.openclaw-agentglue.enabled = false
2. Restart gateway (systemctl --user restart openclaw-gateway)
3. Run all tasks → save baseline results
4. Set plugins.entries.openclaw-agentglue.enabled = true
5. Restart gateway
6. Run same tasks → save agentglue results
7. Generate comparison report
```
