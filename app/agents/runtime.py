from __future__ import annotations

import logging

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.genai import types

from app.logging_config import configure_logging
from app.agents.root import root_agent
from app.config import get_settings


configure_logging()


settings = get_settings()
log = logging.getLogger(__name__)

# Usa VertexAiSessionService quando o projeto GCP estiver configurado (Cloud Run).
# Cai para InMemorySessionService apenas em desenvolvimento local sem projeto.
if settings.google_cloud_project and settings.reasoning_engine_id:
    # Para VertexAiSessionService, o app_name precisa ser um reasoning engine id
    # (ou nome completo do recurso), nao um nome arbitrario.
    _app_name = settings.reasoning_engine_id
    session_service: VertexAiSessionService | InMemorySessionService = VertexAiSessionService(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )
else:
    _app_name = settings.app_name
    session_service = InMemorySessionService()
    if settings.google_cloud_project and not settings.reasoning_engine_id:
        log.warning(
            "REASONING_ENGINE_ID nao configurado; usando InMemorySessionService."
        )

runner = Runner(agent=root_agent, app_name=_app_name, session_service=session_service)
known_sessions: set[tuple[str, str]] = set()


async def run_agent_message(user_id: str, session_id: str, message: str) -> str:
    session_key = (user_id, session_id)
    if isinstance(session_service, InMemorySessionService):
        if session_key not in known_sessions:
            await session_service.create_session(
                app_name=_app_name, user_id=user_id, session_id=session_id
            )
            known_sessions.add(session_key)
    else:
        existing = await session_service.get_session(
            app_name=_app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=_app_name, user_id=user_id, session_id=session_id
            )

    last_agent_text: str = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        # Só nos interessa a resposta final do agente (não function_call/function_response)
        if not getattr(event, "is_final_response", False):
            continue
        content = getattr(event, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        text_parts = [getattr(p, "text", None) for p in parts]
        text = "\n".join(t for t in text_parts if t).strip()
        if text:
            last_agent_text = text

    return last_agent_text
