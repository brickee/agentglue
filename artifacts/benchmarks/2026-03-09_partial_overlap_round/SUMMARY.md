# AgentGlue Benchmark Summary

- label: `2026-03-09_partial_overlap_round`
- target_repo: `/home/ubuntu/.openclaw/workspace/projects/AgentGym`
- scenarios: **repo_exploration, partial_overlap**
- runs: **3**
- dedup_ttl_s: **600.0**

## Scenario aggregates

### repo_exploration

- observed calls / run: **20**
- baseline underlying executions mean: **20**
- agentglue underlying executions mean: **11**
- agentglue calls saved mean: **9**
- agentglue dedup rate mean: **0.45**
- baseline wall clock mean: **186.543 ms**
- agentglue wall clock mean: **102.022667 ms**
- takeaway: Clean overlap case: AgentGlue saves 9.0 executions on average (45.0% dedup rate) on repeated repo search/read/list calls.

Per-tool mean summary:
- `list_files`: observed=4, underlying=2, saves=2, dedup_rate=0.5
- `read_file`: observed=8, underlying=4, saves=4, dedup_rate=0.5
- `search_code`: observed=8, underlying=5, saves=3, dedup_rate=0.375

### partial_overlap

- observed calls / run: **20**
- baseline underlying executions mean: **20**
- agentglue underlying executions mean: **18**
- agentglue calls saved mean: **2**
- agentglue dedup rate mean: **0.1**
- baseline wall clock mean: **182.26 ms**
- agentglue wall clock mean: **166.618 ms**
- takeaway: Messier partial-overlap case: AgentGlue still saves 2.0 executions on average (10.0% dedup rate), but exact-match scope leaves near-miss queries and different line ranges untouched.

Per-tool mean summary:
- `list_files`: observed=4, underlying=3, saves=1, dedup_rate=0.25
- `read_file`: observed=8, underlying=7, saves=1, dedup_rate=0.125
- `search_code`: observed=8, underlying=8, saves=0, dedup_rate=0.0

## Concurrent probe

- underlying_call_count: **1**
- coalesced_calls: **1**
- deduped_calls_in_metrics: **2**
- finding: Single-flight coalescing active: concurrent identical calls share the first execution's result. Underlying executions: 1, coalesced waiters: 1.

## Interpretation

AgentGlue is strongest when multiple agents make truly identical calls close together. Sequential exact matches are handled by the TTL cache; concurrent exact matches are handled by single-flight coalescing. Partial-overlap scenarios remain useful because they show the ceiling of exact-match dedup without pretending semantic dedup already exists.
