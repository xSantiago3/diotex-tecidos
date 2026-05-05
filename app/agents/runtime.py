from __future__ import annotations

import logging
import time

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.genai import errors as genai_errors
from google.genai import types

from app.logging_config import configure_logging
from app.agents.root import root_agent
from app.config import get_settings

try:
    from google.adk.errors.session_not_found_error import SessionNotFoundError as _AiSessionNotFoundError
except ImportError:
    _AiSessionNotFoundError = None  # type: ignore[assignment,misc]


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


async def _ensure_session(user_id: str, session_id: str, base_session_key: tuple[str, str]) -> str:
    """Garante que a sessão existe e retorna o session_id efetivo real.

    O VertexAiSessionService pode ignorar o session_id solicitado e atribuir um ID
    gerado pelo servidor. Esta função captura o ID real e atualiza active_session_ids.
    """
    if isinstance(session_service, InMemorySessionService):
        session_key = (user_id, session_id)
        if session_key not in known_sessions:
            await session_service.create_session(
                app_name=_app_name, user_id=user_id, session_id=session_id
            )
            known_sessions.add(session_key)
        return session_id

    # VertexAiSessionService: verifica se já existe; se não, cria e captura ID real.
    existing = await session_service.get_session(
        app_name=_app_name, user_id=user_id, session_id=session_id
    )
    if existing is not None:
        real_id = getattr(existing, "id", None) or getattr(existing, "session_id", None) or session_id
        if real_id != session_id:
            active_session_ids[base_session_key] = real_id
        return real_id

    try:
        created = await session_service.create_session(
            app_name=_app_name, user_id=user_id, session_id=session_id
        )
        real_id = (
            getattr(created, "id", None)
            or getattr(created, "session_id", None)
            or session_id
        ) if created is not None else session_id
        if real_id != session_id:
            log.info(
                "VertexAI atribuiu session_id=%s (solicitado=%s) para user=%s",
                real_id, session_id, user_id,
            )
            active_session_ids[base_session_key] = real_id
        return real_id
    except genai_errors.ClientError as e:
        if "already exists" not in str(e):
            raise
        # Criado concorrentemente — relê para obter ID real
        existing2 = await session_service.get_session(
            app_name=_app_name, user_id=user_id, session_id=session_id
        )
        if existing2 is not None:
            real_id = getattr(existing2, "id", None) or getattr(existing2, "session_id", None) or session_id
            active_session_ids[base_session_key] = real_id
            return real_id
        return session_id


async def _collect_runner_response(user_id: str, session_id: str, message: str) -> str:
    """Executa runner.run_async e coleta texto final."""
    last_agent_text: str = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
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


async def run_agent_message(user_id: str, session_id: str, message: str) -> str:
    base_session_key = (user_id, session_id)
    effective_session_id = _resolve_session_id(user_id, session_id)

    effective_session_id = await _ensure_session(user_id, effective_session_id, base_session_key)

    try:
        last_agent_text = await _collect_runner_response(user_id, effective_session_id, message)
    except Exception as exc:
        # SessionNotFoundError: sessão não encontrada pelo runner (pode ter sido deletada
        # externamente ou o VertexAI atribuiu ID diferente). Cria sessão nova e tenta uma vez.
        is_session_error = (
            (_AiSessionNotFoundError is not None and isinstance(exc, _AiSessionNotFoundError))
            or "session not found" in str(exc).lower()
        )
        if not is_session_error:
            raise
        log.warning(
            "SessionNotFoundError para user=%s session=%s — criando nova sessão e retentando",
            user_id, effective_session_id,
        )
        new_session_id = f"{session_id}-{int(time.time())}"
        created = await session_service.create_session(
            app_name=_app_name, user_id=user_id, session_id=new_session_id
        )
        effective_session_id = (
            getattr(created, "id", None)
            or getattr(created, "session_id", None)
            or new_session_id
        ) if created is not None else new_session_id
        active_session_ids[base_session_key] = effective_session_id
        if isinstance(session_service, InMemorySessionService):
            known_sessions.add((user_id, effective_session_id))
        last_agent_text = await _collect_runner_response(user_id, effective_session_id, message)

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
