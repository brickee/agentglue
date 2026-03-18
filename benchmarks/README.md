# AgentGlue E2E Benchmark

End-to-end multi-agent benchmark measuring real AgentGlue impact through OpenClaw.

## What it measures

Each task dispatches N sub-agents (via `sessions_spawn`) that perform overlapping work on the AgentGlue repo itself. Metrics collected:

- **Wall-clock time** — total time per task
- **Tool calls** — count by type (read, grep, glob, etc.)
- **Token usage** — input + output tokens
- **Cache hits** — AgentGlue cache hit rate (when plugin active)

## Tasks (10 scenarios)

| ID | Task | Agents | Overlap |
|---|---|---:|---|
| T01 | Code review — dedup middleware | 3 | high |
| T02 | Bug hunt — middleware layer | 4 | high |
| T03 | Document public APIs | 3 | medium |
| T04 | Security audit — full repo | 3 | high |
| T05 | Test coverage gap analysis | 3 | high |
| T06 | Dependency & compatibility check | 2 | medium |
| T07 | Architecture review — cross-cutting | 4 | very-high |
| T08 | Parallel refactoring proposals | 3 | high |
| T09 | Performance bottleneck analysis | 3 | high |
| T10 | New contributor onboarding | 5 | very-high |

Total: 33 sub-agent spawns across all tasks.

## How to run

### Prerequisites

- OpenClaw gateway running: `systemctl --user start openclaw-gateway`
- Python 3.10+

### Step 1: Baseline (no AgentGlue)

Make sure the AgentGlue plugin is NOT installed, then:

```bash
cd /home/ubuntu/.openclaw/workspace/projects/AgentGlue
python3 benchmarks/run_benchmark.py --mode baseline
```

### Step 2: Install AgentGlue

```bash
openclaw plugins install openclaw-agentglue
systemctl --user restart openclaw-gateway
```

### Step 3: With AgentGlue

```bash
python3 benchmarks/run_benchmark.py --mode agentglue
```

### Step 4: Compare

```bash
python3 benchmarks/run_benchmark.py --compare benchmarks/results/baseline_*.json benchmarks/results/agentglue_*.json
```

## Options

```bash
# Run specific tasks only
python3 benchmarks/run_benchmark.py --mode baseline --tasks T01,T04,T07

# Dry run (show tasks, no execution)
python3 benchmarks/run_benchmark.py --mode baseline --dry-run

# Custom timeout per task (default 600s)
python3 benchmarks/run_benchmark.py --mode baseline --timeout 300
```

## Output

Results are saved to `benchmarks/results/` as JSON:
- `baseline_YYYYMMDD_HHMMSS.json`
- `agentglue_YYYYMMDD_HHMMSS.json`
- `comparison_YYYYMMDD_HHMMSS.json`

## Cost estimate

Each full run spawns ~33 sub-agents. Cost depends on model:
- GPT-5.4: ~$2-5 per full run
- GLM-5: ~$0.5-1 per full run
- Kimi K2.5: ~$0.3-0.8 per full run

Running both baseline + agentglue = 2 full runs.
