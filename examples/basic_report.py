#!/usr/bin/env python3
"""Tiny AgentGlue v0.1 example: exact-match cache + single-flight report."""

from __future__ import annotations

import threading
import time

from agentglue import AgentGlue


glue = AgentGlue(shared_memory=False, rate_limiter=False, task_lock=False, dedup_ttl=60.0)
call_count = 0
lock = threading.Lock()
ready = threading.Event()


@glue.tool(ttl=60.0)
def fetch_symbol(symbol: str) -> str:
    global call_count
    ready.set()
    time.sleep(0.05)
    with lock:
        call_count += 1
        current = call_count
    return f"definition:{symbol}:{current}"


results = {}


def worker(agent_id: str) -> None:
    results[agent_id] = fetch_symbol("AgentGlue", agent_id=agent_id)


t1 = threading.Thread(target=worker, args=("agent-a",))
t1.start()
ready.wait(timeout=1.0)
t2 = threading.Thread(target=worker, args=("agent-b",))
t2.start()
t1.join()
t2.join()
results["agent-c"] = fetch_symbol("AgentGlue", agent_id="agent-c")

print("results:")
for agent_id, value in sorted(results.items()):
    print(f"  {agent_id}: {value}")
print(f"underlying_calls: {call_count}")
print()
print(glue.report())

if glue.recorder:
    print("\nlast events:")
    for event in glue.recorder.events[-5:]:
        print(event)
