from __future__ import annotations

from app.config import get_settings
from app.agent_stack.bootstrap import build_agent_stack


settings = get_settings()

_agents = build_agent_stack(settings.default_model)

cart_agent = _agents["cart_agent"]
payment_agent = _agents["payment_agent"]
checkout_orchestrator = _agents["checkout_orchestrator"]
catalog_agent = _agents["catalog_agent"]
sales_agent = _agents["sales_agent"]
support_agent = _agents["support_agent"]
admin_agent = _agents["admin_agent"]
root_agent = _agents["diotex_orchestrator"]