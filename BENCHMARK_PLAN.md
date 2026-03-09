# AgentGlue v0.1 Benchmark Plan

## Recommendation: first scenario to test

**Start with multi-agent repo search / codebase exploration.**

Why this should be the first benchmark:
- It naturally produces repeated tool calls (`search`, `read_file`, `grep`, symbol lookup).
- Dedup + cache can save work immediately without needing sophisticated shared memory.
- It is easy to replay, measure, and explain.
- It matches real multi-agent coding systems better than a toy API benchmark.

In other words: this is the cleanest place to prove AgentGlue is not just middleware-flavored poetry.

## Benchmark goal

Show that AgentGlue v0.1 reduces redundant tool executions and improves effective latency on a shared-tool workload, while preserving task outputs.

## Scenario A — Repo exploration swarm (recommended first)

### Setup
- Target: a medium-sized Python repo (5k-50k LOC).
- Agents: 3-5 workers.
- Shared task: answer a fixed set of engineering questions about the repo.
- Tool surface:
  - `search_code(query)`
  - `read_file(path)`
  - `list_files(path)`
- Execution modes:
  1. **Baseline**: same workload, direct tools, no AgentGlue.
  2. **AgentGlue v0.1**: same workload, tools wrapped with dedup + TTL cache + metrics.

### Example question set
- Where is authentication handled?
- Which modules call the database client?
- Where is retry logic implemented?
- Which tests cover the API layer?
- What config files define runtime settings?

### Expected repeated behavior
- Multiple agents search for the same keywords.
- Multiple agents open the same core files.
- Multiple agents enumerate the same directories.

That’s exactly the redundancy tax AgentGlue should catch.

## Workload protocol

For each run:
1. Reset the repo checkout and benchmark state.
2. Run the same question set with the same number of agents.
3. Collect:
   - total observed tool calls
   - underlying tool executions
   - calls saved
   - cache hit rate
   - wall-clock runtime
   - per-tool call counts
4. Repeat 5-10 times.
5. Report median and p90.

## Primary metrics

### Coordination metrics
- **Observed tool calls**: all wrapper invocations.
- **Underlying executions**: actual tool executions after dedup/cache.
- **Calls saved**: observed - underlying.
- **Dedup rate**: calls saved / observed.
- **Cache hit rate**: cache hits / total lookups.

### Runtime metrics
- **Wall-clock runtime** for the whole workload.
- **Average underlying latency** per tool.
- **Average observed latency** per call.

### Correctness guardrails
- Final answer completeness score (manual rubric is fine for v0.1).
- No benchmark run should fail due to cache corruption or stale return mismatches.

## Success criteria for v0.1

Call it a win if Scenario A shows all of the following:
- **20%+ calls saved** on median runs
- **Clear reduction in underlying executions** for `search_code` and `read_file`
- **No correctness regressions** in final answers
- **Metrics/report output is understandable without log archaeology**

## Nice second scenario

### Scenario B — Partial-overlap repo exploration

Use 3-4 agents working on related but not perfectly identical repo questions. Keep the same basic tool surface:
- search the repo
- read overlapping-but-not-identical file slices
- list nearby directories

What changes:
- some calls are exact duplicates
- some calls are near-misses (`TokenBucket|rate_limit` vs `rate_limit|rate_limited`)
- some reads hit the same file but different line windows

Why this matters:
- it is still realistic for coding-agent swarms
- it shows where exact-match dedup helps
- it also shows what v0.1 does **not** catch yet, which is important for benchmark honesty

This is a better second scenario than a search/read/test loop because it adds realism without importing test-run noise that AgentGlue is not trying to solve.

## What not to benchmark first
- Pure synthetic API spam: too toy.
- Full long-horizon autonomous software engineering: too noisy.
- Shared memory/rate coordination/task lock claims: premature for v0.1.

## Minimal benchmark harness design

A good first harness can be very small:
- Python script that wraps three deterministic repo tools.
- Agent workers driven by a fixed question list.
- JSON output per run with metrics and answers.
- CSV or Markdown summary across repeats.

## Deliverable format

For the first benchmark result, publish:
- repo used
- agent count
- question set
- TTL used
- number of runs
- median metrics table
- one short interpretation paragraph

## Recommended next step

Implement **Scenario A** first and do not overcomplicate it. If AgentGlue cannot win on repo exploration, it has no business making bigger promises yet.
