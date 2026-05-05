from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


ToolCallable = Callable[..., object]


@dataclass(frozen=True)
class ToolContract:
    """Declarative contract for a tool exposed to agents."""

    name: str
    callable_name: str
    description: str = ""
    timeout_seconds: int = 30
    retryable: bool = False


@dataclass(frozen=True)
class AgentContract:
    """Declarative contract that can be adapted to an ADK Agent."""

    name: str
    description: str
    instruction: str
    tools: tuple[str, ...] = ()
    sub_agents: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
