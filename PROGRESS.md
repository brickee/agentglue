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
- Repointed the first repo-exploration workload at a self-contained benchmark fixture and saved artifacts under `artifacts/first_test_2026-03-09/`.
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

## 2026-03-09 — recorder export helper + benchmark sanity guard
- Added a tiny first-class runtime export helper: `AgentGlue.export_events_jsonl(path)`.
- Extended the recorder with small usability helpers:
  - `EventRecorder.export_summary(path)`
  - `summarize_jsonl(path)` for reloading exported event logs later
- Added `examples/recorder_export.py` so the JSONL story is documented by executable example instead of buried in internals.
- Added `scripts/check_benchmark_result.py`, a deliberately lightweight artifact sanity check that validates:
  - per-run metric consistency
  - per-tool summary totals
  - duplicate-analysis alignment with runtime dedup metrics
  - concurrent probe invariants
- Added smoke coverage for JSONL export + reload roundtrip.
- Updated `README.md` and `NEXT_TODO.md` to reflect the new helper/example and the more honest next step on benchmark checks.

### Why this matters
- JSONL export is now a real inspectable workflow rather than a hidden method on the recorder.
- Benchmark artifacts are a little harder to accidentally misread or quietly regress.
- This keeps the wedge narrow: better observability ergonomics and benchmark credibility, not new product sprawl.

## 2026-03-09 — self-contained benchmark sanity path + tiny CI
- Extended `scripts/benchmark_repo_exploration.py` with `--target-repo` so the harness is no longer tied only to one local external checkout.
- Added `tests/fixture_repo/`, a tiny deterministic repo shaped like the benchmark scenarios expect.
- Added smoke coverage that:
  - runs the benchmark harness against the fixture repo
  - validates the produced artifact with `scripts/check_benchmark_result.py`
- Added a minimal GitHub Actions workflow to run:
  - `pytest -q`
  - `examples/basic_report.py`
  - `examples/recorder_export.py`
- Updated `README.md` and `NEXT_TODO.md` to reflect that benchmark sanity is now partly CI-backed rather than purely local.

### Why this matters
- The benchmark path is more credible because it now has a self-contained regression check.
- CI coverage stays intentionally small: verify the narrow v0.1 path, not a bunch of speculative integrations.
- This is a cleaner answer to “can I trust these benchmark artifacts?” than telling people to reproduce a very specific local machine setup.

## 2026-03-09 — explicit standalone rule: no active AgentGym dependency
- Recorded a project rule that AgentGlue must remain fully standalone.
- AgentGym is now treated as deprecated historical origin only, not an active dependency or default benchmark target.
- Active cleanup direction going forward:
  - no runtime dependency on AgentGym
  - no test dependency on AgentGym
  - no benchmark dependency on AgentGym as required/default target
  - no examples requiring AgentGym
  - no outward-facing messaging that implies AgentGlue still depends on AgentGym

## 2026-03-11 — shared-memory metrics tightened, optional path clarified
- Added `SharedMemoryMetrics` dataclass with: writes, reads, hits, misses, stale_reads, private_access_denied.
- Added `hit_rate` property on `SharedMemory`.
- Added `summary()` method returning current state + metrics.
- Updated module docstring to clearly state: OPTIONAL / SCAFFOLDED, NOT part of core v0.1.
- Added tests for metrics and hit_rate computation.
- This makes the optional shared-memory path more honest and measurable without inflating the default product claim.

## 2026-03-09 — standalone benchmark default + narrow runtime defaults
- Flipped `AgentGlue()` defaults to the narrow standalone path:
  - `shared_memory=False`
  - `task_lock=False`
  - `rate_limiter=False` remains unchanged
- Added `tests/benchmark_fixture/`, a neutral self-contained repo for benchmark scenarios so the default harness no longer points at AgentGym.
- Repointed benchmark scripts and first-test narratives to the self-contained fixture / explicit `--target-repo` override path.
- Removed remaining active AgentGym references from runtime/core docstrings and benchmark-facing docs where they were still implying current dependency.
