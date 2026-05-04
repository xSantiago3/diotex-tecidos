from __future__ import annotations

import logging
import time

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.genai import types

from app.logging_config import configure_logging
from app.agents.root import root_agent
from app.config import get_settings


configure_logging()


settings = get_settings()
log = logging.getLogger(__name__)

# VertexAiSessionService exige REASONING_ENGINE_ID válido (id ou resource name completo).
# Sem isso, usa InMemorySessionService para não interromper o atendimento no WhatsApp.
if settings.google_cloud_project and settings.reasoning_engine_id:
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
            "GOOGLE_CLOUD_PROJECT configurado sem REASONING_ENGINE_ID; usando InMemorySessionService."
        )

runner = Runner(agent=root_agent, app_name=_app_name, session_service=session_service)
known_sessions: set[tuple[str, str]] = set()
active_session_ids: dict[tuple[str, str], str] = {}


def _resolve_session_id(user_id: str, session_id: str) -> str:
    """Resolve o session_id efetivo (permite reset sem depender de delete no backend)."""
    return active_session_ids.get((user_id, session_id), session_id)


async def run_agent_message(user_id: str, session_id: str, message: str) -> str:
    base_session_key = (user_id, session_id)
    effective_session_id = _resolve_session_id(user_id, session_id)
    session_key = (user_id, effective_session_id)
    if isinstance(session_service, InMemorySessionService):
        if session_key not in known_sessions:
            await session_service.create_session(
                app_name=_app_name, user_id=user_id, session_id=effective_session_id
            )
            known_sessions.add(session_key)
    else:
        existing = await session_service.get_session(
            app_name=_app_name, user_id=user_id, session_id=effective_session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=_app_name, user_id=user_id, session_id=effective_session_id
            )

    last_agent_text: str = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=effective_session_id,
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

    # Garante consistência caso a sessão efetiva tenha sido criada a partir do ID base.
    active_session_ids[base_session_key] = effective_session_id

    return last_agent_text


async def reset_session(user_id: str, session_id: str) -> None:
    """Apaga e recria a sessão do ADK para um usuário, limpando o histórico."""
    base_session_key = (user_id, session_id)
    old_effective_session_id = _resolve_session_id(user_id, session_id)
    session_key = (user_id, old_effective_session_id)

    # Gera novo session_id efetivo para iniciar histórico limpo.
    new_effective_session_id = f"{session_id}-{int(time.time())}"

    try:
        await session_service.delete_session(
            app_name=_app_name, user_id=user_id, session_id=old_effective_session_id
        )
    except Exception:
        pass  # sessão pode não existir

    if isinstance(session_service, InMemorySessionService):
        known_sessions.discard(session_key)

    existing_new = await session_service.get_session(
        app_name=_app_name, user_id=user_id, session_id=new_effective_session_id
    )
    if existing_new is None:
        await session_service.create_session(
            app_name=_app_name, user_id=user_id, session_id=new_effective_session_id
        )

    if isinstance(session_service, InMemorySessionService):
        known_sessions.add((user_id, new_effective_session_id))

    active_session_ids[base_session_key] = new_effective_session_id
