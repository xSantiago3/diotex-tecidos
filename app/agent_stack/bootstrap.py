from __future__ import annotations

from google.adk.agents import Agent

from app.agent_stack.adapters.adk import build_adk_agent
from app.agent_stack.contracts import ToolContract
from app.agent_stack.registry import AgentRegistry, ToolRegistry
from app.agent_stack.specs.core import ALL_AGENT_CONTRACTS, ALL_TOOL_CONTRACTS
from app.agents import tools as tools_module


def _iter_unique_tool_contracts() -> list[ToolContract]:
    unique_tools: dict[str, ToolContract] = {}
    for tool_contract in ALL_TOOL_CONTRACTS:
        existing = unique_tools.get(tool_contract.name)
        if existing is None:
            unique_tools[tool_contract.name] = tool_contract
            continue
        if existing.callable_name != tool_contract.callable_name:
            raise ValueError(
                f"Conflicting tool contract for {tool_contract.name}: "
                f"{existing.callable_name} != {tool_contract.callable_name}"
            )
    return list(unique_tools.values())


def _build_tool_registry() -> ToolRegistry[object]:
    registry: ToolRegistry[object] = ToolRegistry()
    for tool_contract in _iter_unique_tool_contracts():
        tool_impl = getattr(tools_module, tool_contract.callable_name)
        registry.register(tool_contract.name, tool_impl)
    return registry


def build_agent_stack(model: str) -> dict[str, Agent]:
    """Build all agents from provider-agnostic contracts using ADK adapter."""

    tools = _build_tool_registry()
    agents: AgentRegistry[Agent] = AgentRegistry()

    for contract in ALL_AGENT_CONTRACTS:
        adk_agent = build_adk_agent(
            contract=contract,
            model=model,
            resolve_tool=tools.get,
            resolve_sub_agent=agents.get,
        )
        agents.register(contract.name, adk_agent)

    return {name: agent for name, agent in agents.items()}
