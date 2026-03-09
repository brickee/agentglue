# AgentGlue first real test — repo exploration workflow

## Verdict
**Yes — AgentGlue helped meaningfully on this workload.**

On a small but real multi-agent-style repo exploration run against the self-contained benchmark fixture, AgentGlue v0.1 reduced **underlying tool executions from 20 to 11** by deduplicating repeated `list_files`, `search_code`, and `read_file` calls.

That is **9 calls saved (45%)** with a very understandable trace. For a first pass, that is real signal, not middleware fan fiction.

---

## What was tested

### Target repo
- Repo: `/home/ubuntu/.openclaw/workspace/projects/AgentGlue/tests/benchmark_fixture`
- Shape: medium-ish local Python repo used as a deterministic exploration target

### Workflow shape
I used a **scripted multi-agent repo-exploration workload** rather than a live autonomous agent framework.

That was intentional:
- it keeps the run deterministic and inspectable
- it uses real repo tools and real shell executions
- it still captures the exact duplicate behavior AgentGlue v0.1 is supposed to fix

### Agents and tools
- Agents: **4** (`agent-a` .. `agent-d`)
- Steps per agent: **5** each
- Total observed tool calls: **20**

Wrapped tools used:
- `list_files(path, max_entries)` → backed by `find | sort | head`
- `search_code(pattern, scope, max_hits)` → backed by `grep -RIn -E`
- `read_file(path, start_line, end_line)` → backed by `sed -n`

### Questions / exploration themes
The agents explored overlapping questions around:
- rate limiting / token bucket code
- replay / duplicate-work analysis
- shared-memory policy logic
- benchmark metric aggregation in `runner.py`

This produced natural overlap on core files such as:
- `src/coordination_demo/core/allocator.py`
- `src/coordination_demo/core/replay.py`
- `src/coordination_demo/eval/runner.py`

---

## How it was run

Script:
- `scripts/repo_exploration_first_test.py`

Artifacts:
- JSON summary: `artifacts/first_test_2026-03-09/repo_exploration_first_test.json`
- Event trace: `artifacts/first_test_2026-03-09/agentglue_events.jsonl`

Execution modes:
1. **Baseline**: same workload, direct tools, no AgentGlue
2. **AgentGlue**: same workload, tools wrapped with `AgentGlue(... shared_memory=False, rate_limiter=False, task_lock=False)`

TTL used for this run:
- **600s**

---

## Results

### Baseline
- Observed tool calls: **20**
- Underlying executions: **20**
- Wall clock: **191.704 ms**

### AgentGlue v0.1
- Observed tool calls: **20**
- Underlying executions: **11**
- Calls saved: **9**
- Dedup rate: **45%**
- Cache hit rate: **45%**
- Avg observed latency: **4.994 ms**
- Avg underlying latency: **9.073 ms**
- Wall clock: **101.112 ms**

### Delta vs baseline
- Underlying executions reduced by **45%**
- Wall-clock runtime reduced by about **47%** on this sequential scripted run

Caveat: the wall-clock win is encouraging, but the more robust claim is the execution reduction. Runtime gains will vary more once the workload becomes concurrent and noisier.

---

## Observed duplicate-call patterns

Duplicate intents present in the workload:

1. `list_files(path="src/coordination_demo/core", max_entries=20)`
   - Seen by: `agent-a`, `agent-b`, `agent-d`
   - Duplicates: **2**

2. `read_file(path="src/coordination_demo/core/replay.py", start_line=1, end_line=140)`
   - Seen by: `agent-a`, `agent-b`, `agent-d`
   - Duplicates: **2**

3. `read_file(path="src/coordination_demo/core/allocator.py", start_line=1, end_line=120)`
   - Seen by: `agent-a`, `agent-b`
   - Duplicates: **1**

4. `read_file(path="src/coordination_demo/eval/runner.py", start_line=320, end_line=420)`
   - Seen by: `agent-c`, `agent-d`
   - Duplicates: **1**

5. `search_code(pattern="TokenBucket|rate_limit", scope="src tests", max_hits=20)`
   - Seen by: `agent-a`, `agent-b`
   - Duplicates: **1**

6. `search_code(pattern="replay_duplicate_decomposition|replay_invariant_precheck", scope="src tests", max_hits=20)`
   - Seen by: `agent-a`, `agent-b`
   - Duplicates: **1**

7. `search_code(pattern="semantic_duplicate_work_count|duplicate_tool_calls", scope="src tests", max_hits=20)`
   - Seen by: `agent-c`, `agent-d`
   - Duplicates: **1**

### Duplicates by tool
- `read_file`: **4** saved
- `search_code`: **3** saved
- `list_files`: **2** saved

This is exactly the expected pattern for repo exploration: agents converge on the same hot files and the same grep queries very quickly.

---

## Underlying executions actually observed

Representative real commands executed under the wrapped tools:

- `find "src/coordination_demo/core" -type f | sed 's#^./##' | sort | head -n 20`
- `grep -RIn --binary-files=without-match -E "TokenBucket|rate_limit" src tests | head -n 20`
- `sed -n '1,120p' "src/coordination_demo/core/allocator.py"`
- `grep -RIn --binary-files=without-match -E "replay_duplicate_decomposition|replay_invariant_precheck" src tests | head -n 20`
- `sed -n '1,140p' "src/coordination_demo/core/replay.py"`
- `sed -n '320,420p' "src/coordination_demo/eval/runner.py"`

The event trace shows the expected pattern:
- `agent-a` executes several cold calls
- `agent-b` immediately gets dedup hits on the same calls
- `agent-c` seeds another cluster of cache entries
- `agent-d` hits those entries for repeated `list_files`, `search_code`, and `read_file` requests

---

## Notable findings

### 1. The v0.1 story looks real on the recommended first benchmark
This workload is exactly where exact-match dedup should shine, and it did.

If AgentGlue could not win here, the product story would be in trouble. It won here pretty cleanly.

### 2. `read_file` looks like the highest-value early target
The biggest savings came from repeated reads of the same core files.

That suggests the next benchmark should keep file reads in the critical path rather than focusing only on search.

### 3. Event replay / duplicate analysis needs one cleanup
There is a **schema mismatch** between the current helper in `agentglue.core.recorder.detect_duplicates()` and the runtime event stream.

- The helper currently looks for `event_type == "tool_call"` duplicates.
- The runtime records duplicate hits as `tool_call_deduped`.

Result: the helper reported **zero duplicates** on this run even though the runtime summary correctly reported **9 calls saved**.

That is not a blocker for the runtime itself, but it **is** a blocker for building a benchmark harness on top of that helper without fixing it first.

### 4. The current metrics are readable enough already
The text report plus JSONL trace were sufficient to understand what happened without heroic log archaeology.

That’s a very good sign for v0.1.

---

## Recommendation for next step

**Yes — this is strong enough to justify the lightweight benchmark harness next.**

Recommended immediate next steps:
1. Fix duplicate-trace analysis so recorder helpers understand `tool_call_deduped` events.
2. Turn this script into a tiny reusable benchmark harness with:
   - multiple repeats
   - per-tool summaries
   - markdown/CSV output
3. Add one slightly messier scenario with partial-overlap queries to see how much exact-match dedup leaves on the table.
4. Only after that, decide whether semantic dedup is worth chasing.

My read: **exact-match dedup already earns its keep on repo exploration.** The next job is to make the benchmark repeatable and hard to argue with.

---

## Files added
- `scripts/repo_exploration_first_test.py`
- `artifacts/first_test_2026-03-09/repo_exploration_first_test.json`
- `artifacts/first_test_2026-03-09/agentglue_events.jsonl`
- `TEST_RESULT_2026-03-09.md`
