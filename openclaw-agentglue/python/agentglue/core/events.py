"""Event schema for AgentGlue runtime.

Uses real timestamps for a lightweight runtime event stream.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Event:
    """A single event in the AgentGlue event stream."""

    timestamp: float = field(default_factory=time.monotonic)
    event_type: str = ""
    agent_id: str = ""
    tool_name: str = ""
    correlation_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }
