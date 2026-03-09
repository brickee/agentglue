# AgentGlue — Go-to-market / communication strategy

## Core principle

Because this project does not start with platform distribution, institutional backing, or brand leverage, it must win on **clarity, evidence, and repeatable usefulness**.

Do not try to win by sounding bigger.
Win by being sharper.

## What to sell

Do **not** lead with a grand unified story about solving all multi-agent coordination problems.

Lead with the narrow, credible wedge:

> **AgentGlue reduces redundant shared-tool executions across multiple agents.**
>
> Start with exact-match dedup, TTL caching, and built-in observability.

That is the message people can understand, remember, and try.

## What not to sell

Do not position AgentGlue primarily as:
- a new agent framework
- a new orchestrator
- a universal multi-agent runtime
- a complete shared-memory substrate
- a broad operating system for agent swarms

Those stories are too large relative to the current evidence base.

## Positioning

Best current positioning:

> **A thin runtime layer for shared tool-call coordination in multi-agent coding and retrieval workflows.**

More concrete phrasing:
- stop paying for the same tool call twice
- reduce repeated search/read/list calls across agents
- make shared-tool waste visible and measurable

## Messaging hierarchy

### Level 1 — one-sentence positioning

Use lines like:

- `AgentGlue is a thin runtime layer that reduces redundant shared-tool executions across multiple agents.`
- `AgentGlue helps multi-agent systems stop paying for the same tool call twice.`
- `Dedup, cache, and observe shared tool usage across multiple agents.`

### Level 2 — the concrete pain

Lead with problems people already feel:

- duplicate searches
- repeated file reads
- repeated directory scans
- invisible coordination waste

A good framing:

> Multi-agent frameworks are good at orchestration.
> They are much worse at coordinating shared tool usage.

### Level 3 — the evidence

Use real benchmark/test outcomes as credibility.

Current examples:

> On a clean repo-exploration workload, AgentGlue reduced underlying tool executions from 20 to 11 (45% saved).

> On a messier partial-overlap workload, AgentGlue still saved work — but less — because v0.1 only merges exact matches, not merely similar queries or different file slices.

Evidence should always outrank adjectives.

## GTM rule: sell the waste reduction, not the worldview

The safest and strongest communication rule for this project is:

> **Sell the waste reduction, not the worldview.**

Do not start with philosophy.
Start with observable, compressible waste.

## Who to target first

Prioritize people who are most likely to actually install and test the project.

### Best early audiences

1. **Multi-agent coding / SWE builders**
   - repeated repo search
   - repeated file reads
   - overlapping test/search workflows

2. **Retrieval-heavy agent builders**
   - shared search/fetch/read pipelines
   - repeated tool calls across agents

3. **Infra-minded agent framework users**
   - people who like thin, composable runtime improvements

### Lower-priority audiences for now

- broad “AI agents” audiences with no concrete shared-tool pain
- people who mainly respond to vision narratives
- users looking for a full agent platform replacement

## Communication style

Use a tone of:
- engineering honesty
- benchmark-driven credibility
- narrow claims
- explicit caveats

Good style:
- hypothesis -> test -> result -> interpretation
- say what worked
- say what did not yet work
- say what the next proof step is

Bad style:
- giant future roadmap as if it already exists
- buzzwords outranking measured results
- claiming generality from a single favorable demo

## Recommended communication assets

Before broad promotion, maintain these core assets inside the repo:

1. **Short positioning sentence**
2. **README first-screen pain example**
3. **One benchmark/result statement**
4. **Minimal quickstart**
5. **Tiny working example**
6. **Clear “good fit / not a fit” section**

## README first-screen goals

A visitor should understand within a few seconds:
- what AgentGlue does
- what problem it solves
- how it is adopted
- why the claim is credible

Suggested sequence:
1. one-sentence positioning
2. concrete repeated-tool-call example
3. one benchmark result
4. minimal quickstart
5. roadmap only after the above

## Build-in-public strategy

Without existing platform leverage, distribution should come from a sequence of small, credible public findings rather than one big “launch” moment.

Preferred content pattern:
- what waste pattern was observed
- what was built to test/fix it
- what the benchmark showed
- what caveat was discovered
- what the next iteration should test

Examples:
- repeated repo exploration causes duplicated file reads/searches
- first benchmark: 45% fewer underlying executions
- concurrency probe: cache-after-first-call is not enough
- next step: single-flight / in-flight coalescing

This creates credibility through accumulated evidence.

## Practical channel strategy

Prefer places where technically relevant people will see the project and understand the pain immediately.

Good channels:
- GitHub README as landing page
- X/Twitter posts aimed at coding-agent / infra builders
- Hacker News, if the post is concrete and benchmark-backed
- selective Reddit / engineering communities when the angle is specific

Do not optimize for maximum audience first.
Optimize for the right audience remembering the project correctly.

## Core communication constraints

Always keep these truths visible:
- current wedge is dedup + cache + observability
- benchmark quality matters more than roadmap breadth
- shared memory / rate coordination / task locks should not outrun evidence
- one clean measured claim is better than five speculative categories

## Suggested current tagline

> **AgentGlue is a thin runtime layer that reduces redundant shared-tool executions across multiple agents, starting with exact-match dedup, TTL caching, and built-in observability.**

## Internal reminder

If communication ever starts sounding more ambitious than the benchmark evidence, tighten the claim immediately.

The path to adoption is:
1. narrow pain
2. simple integration
3. measured result
4. repeated proof
5. only then broader story
