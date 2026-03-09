# Next TODO

## Immediate
1. ~~Single-flight / in-flight coalescing~~ — **Done.** Concurrent identical calls now share the leader's result via `_InFlight` + `threading.Event`. Metrics track `tool_calls_coalesced`; events include `tool_call_coalesced`.
2. ~~Add one tiny example script under `examples/` that prints a real v0.1 report.~~
3. Decide whether shared-memory auto-publish should remain enabled by default or become opt-in for a cleaner v0.1 story.
4. ~~Record repeated baseline vs AgentGlue v0.1 metrics on at least one medium-sized Python repo.~~

## Next
5. Remove remaining active AgentGym coupling from benchmark artifacts, docs, and default project narratives.
6. Tighten shared-memory metrics on the runtime path.
7. Decide whether shared-memory auto-publish should remain enabled by default or become opt-in for a cleaner v0.1 story.
8. Add semantic dedup only if exact-match dedup leaves obvious savings on the table.
9. Add a minimal integration adapter skeleton (likely CrewAI or LangGraph).
10. Improve rate-limit ergonomics if the benchmark shows real pressure there.
11. ~~Decide whether the lightweight benchmark sanity check should stay local-only or become a small CI guard once artifact stability feels boring.~~ — **Done.** The benchmark harness now supports `--target-repo`, smoke coverage runs it against `tests/fixture_repo`, and a tiny GitHub Actions workflow covers pytest + executable examples.
