<p align="center">
  <img src="docs/agentglue-banner.svg" alt="AgentGlue banner" width="100%" />
</p>

<h1 align="center">AgentGlue</h1>
<p align="center"><strong>A runtime layer for multi-agent tool coordination.</strong></p>

<p align="center">
  <a href="https://github.com/brickee/AgentGlue/releases"><img src="https://img.shields.io/github/v/release/brickee/AgentGlue?display_name=tag" alt="GitHub release" /></a>
  <a href="https://www.npmjs.com/package/openclaw-agentglue"><img src="https://img.shields.io/npm/v/openclaw-agentglue" alt="npm" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" /></a>
</p>

<p align="center">
  <a href="#highlights-on-the-benchmark-suite">Highlights</a> •
  <a href="#vision">Vision</a> •
  <a href="#supported-today">Supported today</a> •
  <a href="#ongoing">Ongoing</a> •
  <a href="#install">Install</a> •
  <a href="#openclaw-plugin">OpenClaw plugin</a>
</p>

AgentGlue sits one layer below orchestration frameworks. It does **not** decide which agent should act. It makes sure that when many agents hit the same tools, files, or APIs, they do not waste work by blindly repeating the same call.

## Highlights on the benchmark suite

Measured on the current **100-test sidecar benchmark suite** and multi-agent overlap scenarios:

- **3.7× overall speedup** across benchmarked workloads
- **73% total time saved** (`866.4ms → 234.9ms`)
- **76% cache hit rate** across repeated shared work
- **6.8× speedup** on overlapping search-heavy scenarios
- **5.0× speedup** in the 10-agent heavy-overlap case
- **~0.6ms median cache-check latency**

> The pattern is simple: more agents + more overlap = bigger wins.

| Benchmark highlight | Result |
|---|---:|
| Overall speedup | **3.7×** |
| Total time saved | **73%** |
| Cache hit rate | **76%** |
| Best search-heavy case | **6.8×** |
| Best heavy-overlap case | **5.0×** |
| Median cache-check latency | **0.6ms** |

## Vision

The long-term goal is straightforward:

> **Make multi-agent systems behave less like a swarm of amnesiac interns and more like a coordinated runtime.**

That means:
- deduplicating identical tool calls before they waste time and tokens
- sharing useful state across agents when the overlap is exact and actionable
- giving teams observability into where coordination waste is actually coming from
- staying framework-agnostic, so the runtime can sit under OpenClaw, AutoGen, CrewAI, LangGraph, or custom stacks

## Supported today

### Core runtime
- **Exact-match dedup** on tool name + args/kwargs hash
- **Single-flight / in-flight coalescing** for concurrent identical calls
- **TTL result cache** for repeated sequential calls
- **SQLite backend** for cross-process cache sharing
- **Cache invalidation** and full cache clearing
- **Metrics + event recording** for runtime inspection

### OpenClaw plugin
- npm package: [`openclaw-agentglue`](https://www.npmjs.com/package/openclaw-agentglue)
- auto-managed Python sidecar
- cache-aware repo tools:
  - `agentglue_cached_read`
  - `agentglue_cached_search`
  - `agentglue_cached_list`
- `after_tool_call` hook for automatic caching of read-only tool results
- health + metrics endpoints

### Design constraints
- **Framework-agnostic** by design
- **Standalone** — no active AgentGym dependency
- **Exact-match only** for now; AgentGlue does not pretend similar calls are the same thing

## Ongoing

What is actively in progress or intentionally next:

- tighter shared-memory semantics and tests
- richer rate coordination / backpressure story
- task-lock conflict prevention path
- clearer first-class integrations for frameworks beyond OpenClaw
- broader benchmark coverage beyond the current overlap-heavy wedge

This repo is deliberately taking the **narrow wedge** route: prove value on duplicated shared work first, then widen the surface only when the evidence says it is worth it.

## How it works

```text
Agent framework / orchestrator
          ↓
       AgentGlue
   (dedup + cache + metrics)
          ↓
      tools / APIs
```

If two agents make the **same** tool call at the same time, one execution leads and the others wait.
If a later agent makes the **same** call again within TTL, it gets the cached result.
If the calls are merely similar, AgentGlue does nothing clever and does not bluff.

## Install

### 1) OpenClaw plugin via npm

```bash
npm install -g openclaw-agentglue
# or
openclaw plugins install openclaw-agentglue
```

Plugin docs: [`openclaw-agentglue/README.md`](./openclaw-agentglue/README.md)

### 2) Clone the repo

```bash
git clone https://github.com/brickee/AgentGlue.git
cd AgentGlue
```

### 3) Install the Python package

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## Quick Python example

```python
from agentglue import AgentGlue

glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False)

@glue.tool(ttl=60)
def search_code(query: str) -> str:
    print(f"real search: {query}")
    return f"results for {query}"

print(search_code("sqlite sidecar", agent_id="agent-a"))
print(search_code("sqlite sidecar", agent_id="agent-b"))  # dedup / cache hit
print(glue.report())
```

## OpenClaw plugin

For OpenClaw users, AgentGlue already ships as a self-contained plugin:

- **npm:** <https://www.npmjs.com/package/openclaw-agentglue>
- **plugin docs:** [`openclaw-agentglue/README.md`](./openclaw-agentglue/README.md)
- **repo:** <https://github.com/brickee/AgentGlue/tree/main/openclaw-agentglue>

What the plugin adds:
- cross-process cache sharing through a SQLite-backed sidecar
- auto-start / health monitoring / restart handling
- cache-aware repo exploration tools for sub-agents
- zero separate runtime `pip install` step for the bundled plugin path

## Why this exists

Most multi-agent tooling focuses on orchestration. Fair enough. But a surprising amount of waste comes from the layer underneath: repeated reads, repeated searches, repeated API calls, and no shared memory of what just happened.

AgentGlue is the runtime answer to that problem.

## Releases

- **Latest GitHub releases:** <https://github.com/brickee/AgentGlue/releases>
- **OpenClaw npm plugin:** <https://www.npmjs.com/package/openclaw-agentglue>

## License

MIT
