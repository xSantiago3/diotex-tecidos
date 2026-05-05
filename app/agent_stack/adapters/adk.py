from __future__ import annotations

from collections.abc import Callable

from google.adk.agents import Agent

from app.agent_stack.contracts import AgentContract


def build_adk_agent(
    *,
    contract: AgentContract,
    model: str,
    resolve_tool: Callable[[str], object],
    resolve_sub_agent: Callable[[str], Agent],
) -> Agent:
    """Adapter that turns a provider-agnostic AgentContract into an ADK Agent."""

    tools = [resolve_tool(name) for name in contract.tools]
    sub_agents = [resolve_sub_agent(name) for name in contract.sub_agents]

    return Agent(
        name=contract.name,
        model=model,
        description=contract.description,
        instruction=contract.instruction,
        tools=tools,
        sub_agents=sub_agents,
    )
