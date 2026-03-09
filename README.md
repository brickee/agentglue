# AgentGlue

> Thin runtime layer for shared tool-call coordination in multi-agent systems — exact-match dedup, TTL cache, single-flight, and baseline observability.

## The problem

Multi-agent frameworks (AutoGen, CrewAI, LangGraph) are good at orchestration — who does what, in what order. Production waste often happens one layer lower: when multiple agents touch the same tools, APIs, files, and shared state without coordination.

That creates a predictable set of problems:
- **Duplicate work** — multiple agents make the same tool calls.
- **Rate limit storms** — agents independently hammer the same external service.
- **Memory blindness** — one agent learns something useful, others rediscover it expensively.
- **Task conflicts** — agents collide on the same file, task, or shared resource.
- **Observability gaps** — you cannot tell where the waste is coming from.

## v0.1 scope

The first usable pass stays intentionally narrow:
- exact-match tool-call dedup
- **in-flight coalescing (single-flight)** — concurrent identical calls share one execution
- TTL result cache for sequential repeat calls
- cache invalidation API
- baseline metrics + event recording
- simple decorator API

What this means in plain English:
- if two agents make the **same call at the same time**, single-flight lets one lead and the others wait
- if another agent makes the **same call shortly after**, the TTL cache serves it
- if the calls are only *similar* rather than identical, AgentGlue v0.1 does **not** merge them

Shared memory, rate coordination, and task locks are scaffolded in the codebase, but they are **not** the product claim for v0.1.

## Architecture

```text
Agent Framework (AutoGen / CrewAI / LangGraph / custom)
    |
[AgentGlue] <-- dedup, cache, baseline observability
    |
Tools / APIs
```

## Quickstart

```python
from agentglue import AgentGlue

# Keep v0.1 tight: dedup + cache + observability
# Disable the other middleware unless you are actively experimenting.
glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False)

@glue.tool(ttl=300)
def search_code(query: str) -> str:
    print(f"real search for: {query}")
    return f"results for {query}"

print(search_code("rate limiter", agent_id="agent-a"))
print(search_code("rate limiter", agent_id="agent-b"))  # dedup hit
print(search_code("cache invalidation", agent_id="agent-c"))

print(glue.report())
```

Example output:

```text
real search for: rate limiter
real search for: cache invalidation
AgentGlue Report:
  Observed tool calls:      3
  Underlying executions:    2
  Calls saved by dedup:     1/3 (33%)
  Cache hit rate:           33%
  Avg observed latency:     0.01 ms
  Avg underlying latency:   0.01 ms
  Rate limit interventions: 0
  Shared memory writes:     0
  Shared memory hits:       0
  Task conflicts prevented: 0
```

## API

### Wrap a tool

```python
from agentglue import AgentGlue

glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False)

@glue.tool(ttl=60)
def fetch_doc(path: str) -> str:
    return open(path).read()
```

### Invalidate a single cached result

```python
glue.invalidate("fetch_doc", "README.md")
```

### Clear the cache

```python
glue.clear_cache()
```

### Access summary metrics programmatically

```python
summary = glue.summary()
print(summary["calls_saved"])
print(summary["cache_hit_rate"])
```

## What v0.1 measures

- observed tool calls
- underlying tool executions
- calls saved by dedup
- **coalesced calls (single-flight)**
- dedup rate
- cache hit rate
- average observed latency
- average underlying latency
- rate-limit intervention count
- shared-memory write count
- task-conflict prevention count

## Current status

**Usable v0.1 path is implemented** for decorator-based dedup + cache + baseline observability.

What is working now:
- exact-match dedup keyed by tool name + args/kwargs hash
- in-flight coalescing (single-flight): concurrent identical calls wait for the leader's result
- TTL expiry
- cache invalidation and full-cache clearing
- text report + dict summary (includes `tool_calls_coalesced` metric)
- event recording for tool calls, dedup hits, coalesced waits, and completions
- smoke tests covering the main path including concurrent single-flight

What remains intentionally deferred:
- semantic dedup
- production-grade shared memory
- real cross-agent rate coordination policy layer
- task-lock productization
- framework integration adapters

## Benchmark recommendation

The first benchmark should be **multi-agent repo search / codebase exploration**.

Why:
- repeated search/read/list calls happen naturally
- dedup value is obvious and measurable
- it mirrors real multi-agent coding systems better than toy API spam
- it avoids the noise of long-horizon autonomous SWE tasks

See [`BENCHMARK_PLAN.md`](./BENCHMARK_PLAN.md) for the concrete plan.

Current benchmark harness:

```bash
PYTHONPATH=src python3 scripts/benchmark_repo_exploration.py --runs 3 --label local_run
```

That writes stable JSON/JSONL/Markdown artifacts under `artifacts/benchmarks/<label>/`, including:
- repeated baseline vs AgentGlue runs
- **two scenarios**: a clean repo-exploration overlap path and a messier partial-overlap path
- per-tool summaries
- recorder-backed duplicate analysis
- scenario-specific JSONL event exports
- a concurrent identical-call probe

The concurrent probe confirms single-flight coalescing: two simultaneous identical calls result in 1 underlying execution and 1 coalesced waiter.

The partial-overlap scenario is there on purpose: it shows where exact-match dedup stops helping, so the benchmark story stays honest instead of quietly grading itself on the easiest possible test forever.

## Design principles

1. **Thin runtime, not a framework**
2. **Framework agnostic**
3. **Observable by default**
4. **Incremental adoption**
5. **No hidden LLM calls inside middleware**

## Development

Run the tiny example:

```bash
PYTHONPATH=src python3 examples/basic_report.py
```

Run the smoke tests with:

```bash
PYTHONPATH=src python3 tests/test_smoke.py
```

If you have pytest installed:

```bash
PYTHONPATH=src pytest -q
```

Export benchmark artifacts:

```bash
PYTHONPATH=src python3 scripts/benchmark_repo_exploration.py --runs 3 --label local_run
```

## Disclaimer

This project is a personal open-source project developed in my personal capacity. It is not affiliated with, endorsed by, or representing any employer or organization I am associated with. All work on this project is performed on personal time and with personal resources.

## License

MIT
