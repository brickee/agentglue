# AGENTS.md — AgentGlue project workflow

## Execution rule

For this project, every development iteration must end cleanly:

1. finish the intended round of work
2. run the relevant tests / verification
3. commit the changes
4. push to `origin/main`
5. only then report completion

Do not leave completed work only in the local working tree.
Do not report a round as done if it has not been both committed and pushed.

## Product focus

Keep AgentGlue focused on the narrow wedge unless explicitly changed:
- exact-match dedup
- TTL cache
- observability
- benchmark credibility
- concurrent identical-call behavior / single-flight when justified

Avoid premature expansion into broad shared-memory / rate-coordination / task-lock product claims unless supported by evidence.
