from __future__ import annotations

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.root import root_agent
from app.config import get_settings


settings = get_settings()
session_service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name=settings.app_name, session_service=session_service)
known_sessions: set[tuple[str, str]] = set()


async def run_agent_message(user_id: str, session_id: str, message: str) -> str:
    session_key = (user_id, session_id)
    if session_key not in known_sessions:
        await session_service.create_session(app_name=settings.app_name, user_id=user_id, session_id=session_id)
        known_sessions.add(session_key)

    final_text_parts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        content = getattr(event, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                final_text_parts.append(text)

    return "\n".join(part for part in final_text_parts if part).strip()
