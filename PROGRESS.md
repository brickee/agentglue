# Progress Log

## 2026-03-09 — v0.1 first usable pass
- Tightened the product story around the real v0.1 surface: exact-match dedup + TTL cache + baseline observability.
- Updated `AgentGlue` runtime to expose:
  - decorator-wrapped tools
  - single-entry cache invalidation
  - full cache clearing
  - event payloads with call hashes and completion latency
- Improved metrics to report:
  - observed tool calls
  - underlying executions
  - calls saved
  - dedup rate
  - cache hit rate
  - basic latency averages
- Kept shared memory, rate limiting, and task lock as scaffolded modules rather than overclaiming them as v0.1-complete features.
- Added `tests/conftest.py` so the test suite works from a source checkout without installing the package first.
- Expanded smoke tests to cover:
  - dedup hit path
  - TTL expiry
  - invalidation
  - event recording
  - updated metrics/report output
- Added `BENCHMARK_PLAN.md` with a concrete evaluation plan and a strong recommendation to start with multi-agent repo search / codebase exploration.
- Updated README / PLAN / NEXT_TODO to match the new status.

## 2026-03-09 — first real repo-exploration test
- Added `scripts/repo_exploration_first_test.py`, a deterministic multi-agent-style repo exploration workload using real shell-backed tools:
  - `list_files`
  - `search_code`
  - `read_file`
- Ran the workload against the local `AgentGym` repo and saved artifacts under `artifacts/first_test_2026-03-09/`.
- Wrote `TEST_RESULT_2026-03-09.md` with the first concrete result.

### First result
- Baseline: **20 observed / 20 underlying**
- AgentGlue: **20 observed / 11 underlying**
- Calls saved: **9**
- Dedup rate: **45%**
- Cache hit rate: **45%**
- Wall-clock on this scripted run dropped from **191.7 ms** to **101.1 ms**

## 2026-03-09 — dedup observability cleanup + benchmark harness
- Fixed `agentglue.core.recorder.detect_duplicates()` so it understands runtime `tool_call_deduped` events instead of only raw `tool_call` events.
- Added richer duplicate summaries with per-agent, per-tool, and per-intent breakdowns suitable for benchmark artifacts.
- Extended smoke coverage to verify:
  - recorder analysis matches runtime dedup events
  - concurrent identical calls do **not** currently single-flight / coalesce in flight
- Added `scripts/benchmark_repo_exploration.py`, a lightweight reusable harness with:
  - multiple runs
  - stable JSON output
  - metadata block
  - per-tool summaries
  - JSONL event exports
  - a dedicated concurrent probe
- Ran the harness and saved artifacts under `artifacts/benchmarks/2026-03-09_dedup_observability/`.

### What changed in my understanding
- Exact-match dedup remains meaningfully useful on repo exploration; the repeated result is stable enough to inspect rather than hand-wave.
- The observability bug was small but real: the old duplicate helper undercounted exactly the thing the runtime was saving.
- Concurrency evidence is now explicit: current dedup is **cache-after-first-call**, not in-flight coalescing.

### Current status
- The benchmark path is now lightweight but reusable rather than one-off.
- Main working story: wrap repo-exploration tools, save repeated calls, inspect metrics, and inspect JSONL traces with the recorder analysis aligned to the runtime schema.
- Best next move if AgentGlue wants a stronger multi-agent claim: add straightforward single-flight / in-flight coalescing for identical calls.

## 2026-03-09 — honest single-flight positioning + broader benchmark coverage
- Tightened outward-facing messaging in `README.md`, `GO_TO_MARKET.md`, and package metadata so the v0.1 claim is explicit:
  - exact-match dedup
  - TTL cache for sequential repeats
  - single-flight for concurrent identical calls
  - no claim that merely similar calls are merged
- Added `examples/basic_report.py`, a tiny inspectable script that shows:
  - one concurrent identical-call coalescing event
  - one later cache hit
  - the real `glue.report()` output
- Extended `scripts/benchmark_repo_exploration.py` to support multiple scenarios instead of only the clean path.
- Added a second benchmark scenario, `partial_overlap`, where agents ask related-but-not-identical questions:
  - overlapping directories
  - overlapping files with different line windows
  - similar but non-identical grep patterns
- Kept artifact generation simple and inspectable:
  - one `result.json`
  - one `SUMMARY.md`
  - one JSONL event log per scenario
  - one JSONL event log for the concurrent single-flight probe

### Why this matters
- The benchmark story is stronger because it no longer relies only on the cleanest possible overlap case.
- The messaging is tighter because it now says exactly when AgentGlue helps and when it doesn’t.
- The example lowers the cost of inspection for anyone evaluating whether the current v0.1 story is real.
