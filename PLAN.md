# AgentGlue — Project Plan

## North Star

Build a framework-agnostic runtime middleware for multi-agent systems that eliminates coordination waste around shared tools and state with zero intrusion into existing agent logic.

## v0.1 product claim

Ship the thinnest version that proves value on the most common, measurable failure mode:
- duplicate tool calls
- repeated identical reads/searches
- invisible coordination waste

For v0.1, the real product surface is:
- decorator-based tool wrapping
- exact-match dedup
- TTL cache
- invalidation API
- baseline observability via metrics + event recording

Shared memory, rate coordination, and task locks remain part of the roadmap, but they should not be treated as product-complete in the first release.

## Architecture

```text
Agent Framework
    |
    v
AgentGlue Runtime
  ├── ToolProxy / decorator — intercepts tool calls, applies dedup + cache
  ├── EventRecorder         — in-memory event stream for v0.1 observability
  ├── GlueMetrics           — reports observed calls, underlying executions, cache hit rate
  ├── SharedMemory          — scaffolded, not a v0.1 claim
  ├── RateLimiter           — scaffolded, not a v0.1 claim
  └── TaskLock              — scaffolded, not a v0.1 claim
    |
    v
Tools / APIs
```

## Milestones

### M0 — Project Foundation
- [x] Repo scaffolding
- [x] README with vision and API sketch
- [x] PLAN.md / PROGRESS.md / NEXT_TODO.md
- [x] Import the minimal core helpers needed for allocator / events / recorder / metrics
- [x] pyproject.toml + basic package structure

### M1 — v0.1: Dedup + Cache + Baseline Observability
- [x] Decorator-based tool wrapping as the primary API surface
- [x] Exact-match dedup (tool name + args/kwargs hash)
- [x] TTL-based result cache
- [x] Cache invalidation API
- [x] Baseline observability enabled by default for the wrapped path
- [x] Metrics: total tool calls, underlying executions, dedup hit rate, cache hit rate, calls saved, basic latency
- [x] Summary report generator (text + dict)
- [x] Smoke tests for the main path
- [x] README examples aligned with current implementation

### M1.5 — Benchmark & Value Proof
- [x] Concrete benchmark plan documented
- [x] Build a minimal, reproducible benchmark harness around shared-tool multi-agent workloads
- [x] Preferred first scenario: multi-agent repo search / codebase exploration
- [x] Measure: observed calls, underlying executions, calls saved, wall-clock time, cache hit rate
- [x] Publish first benchmark results
- [x] Add recorder analysis that matches runtime dedup events (`tool_call_deduped`)
- [x] Add a concurrent identical-call probe to test cache-vs-coalescing behavior
- [ ] Optional second scenario: SWE-style search/read/test loop
- [ ] Decide whether single-flight coalescing belongs in the near-term core path

### M2 — Shared Memory
- [ ] Tighten `SharedMemory` semantics (TTL, confidence, scoping)
- [ ] Decide whether auto-publish should stay default-on or become explicit
- [ ] Add metrics hooks for reads/writes/hits/misses on the real runtime path
- [ ] Tests

### M3 — Rate Coordination
- [ ] Promote `RateLimiter` into a clearer `RateCoordinator` story if warranted
- [ ] Shared token buckets across agents
- [ ] Backpressure policies: wait / retry / drop
- [ ] Metrics: interventions, wait time
- [ ] Tests

### M4 — Task Locks & Conflict Prevention
- [ ] Intent declaration API
- [ ] Conflict detection before work starts
- [ ] Dead-intent cleanup semantics
- [ ] Metrics: conflicts detected, prevented
- [ ] Tests

### M5 — Integrations & Advanced Observability
- [ ] JSONL event export demo in docs
- [ ] CrewAI integration adapter skeleton
- [ ] LangGraph integration adapter skeleton
- [ ] AutoGen integration adapter skeleton
- [ ] Richer traces / dashboards only after benchmark proof

## Non-goals for v0.1
- Not a new agent framework
- Not an orchestrator
- No hidden LLM calls inside middleware
- No distributed deployment story yet
- No claim of semantic dedup yet

## Operating rules
1. Keep the API surface minimal.
2. Every shipped behavior needs tests.
3. Docs must match what actually works.
4. Benchmark before broadening the product story.
