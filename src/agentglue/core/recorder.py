"""Event recording and duplicate detection.

Used for observability and post-hoc analysis of multi-agent coordination.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


class EventRecorder:
    """Records events to an append-only log."""

    def __init__(self):
        self.events: List[Dict] = []

    def record(self, event_dict: Dict) -> None:
        self.events.append(event_dict)

    def dump_jsonl(self, path: str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return p

    def export_summary(self, path: str) -> Dict[str, Any]:
        """Write the current event stream to JSONL and return a small summary.

        Intended as a tiny usability helper for benchmark/debug workflows so a
        caller can export one file and immediately inspect duplicate analysis
        without re-plumbing the recorder internals.
        """
        exported_path = self.dump_jsonl(path)
        return {
            "path": str(exported_path),
            "event_count": len(self.events),
            "duplicate_analysis": detect_duplicates(self.events),
        }

    def clear(self) -> None:
        self.events.clear()


def load_jsonl(path: str) -> List[Dict]:
    p = Path(path)
    out: List[Dict] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out


def summarize_jsonl(path: str) -> Dict[str, Any]:
    """Load a JSONL event log and return a compact duplicate-oriented summary."""
    events = load_jsonl(path)
    return {
        "path": str(Path(path)),
        "event_count": len(events),
        "duplicate_analysis": detect_duplicates(events),
    }


def _event_args_hash(event: Dict[str, Any]) -> str:
    return event.get("payload", {}).get("args_hash", "")


def detect_duplicates(events: List[Dict]) -> Dict[str, Dict]:
    """Detect duplicate tool-call intents across the runtime event stream.

    AgentGlue records:
    - ``tool_call`` for underlying executions
    - ``tool_call_deduped`` for calls served from the dedup cache

    This helper normalizes both into a benchmark-facing view. If the stream only
    contains repeated ``tool_call`` events, it falls back to treating all but the
    first as duplicate intents. When ``tool_call_deduped`` is present, those are
    treated as the saved calls.
    """
    intent_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    for event in events:
        if event.get("event_type") not in {"tool_call", "tool_call_deduped"}:
            continue
        tool_name = event.get("tool_name", "")
        args_hash = _event_args_hash(event)
        intent_map[(tool_name, args_hash)].append(event)

    by_agent: Dict[str, int] = defaultdict(int)
    by_tool: Dict[str, int] = defaultdict(int)
    total_saved = 0
    intent_summaries: List[Dict[str, Any]] = []

    for (tool_name, args_hash), calls in intent_map.items():
        observed = len(calls)
        deduped_calls = [event for event in calls if event.get("event_type") == "tool_call_deduped"]
        underlying_calls = [event for event in calls if event.get("event_type") == "tool_call"]

        duplicates = len(deduped_calls)
        if duplicates == 0 and observed > 1:
            duplicates = observed - 1

        if duplicates <= 0:
            continue

        total_saved += duplicates
        by_tool[tool_name] += duplicates

        duplicate_agents = deduped_calls or calls[1:]
        for event in duplicate_agents:
            agent = event.get("agent_id", "unknown")
            by_agent[agent] += 1

        intent_summaries.append(
            {
                "tool_name": tool_name,
                "args_hash": args_hash,
                "observed_calls": observed,
                "underlying_calls": len(underlying_calls),
                "deduped_calls": len(deduped_calls),
                "duplicates": duplicates,
                "agents": [event.get("agent_id", "unknown") for event in calls],
            }
        )

    intent_summaries.sort(
        key=lambda item: (
            -item["duplicates"],
            item["tool_name"],
            item["args_hash"],
        )
    )

    return {
        "by_agent": dict(sorted(by_agent.items())),
        "by_tool": dict(sorted(by_tool.items())),
        "total_duplicates": total_saved,
        "duplicate_intents": intent_summaries,
    }
