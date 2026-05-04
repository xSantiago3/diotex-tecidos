from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.schemas import CustomerUpsert


def extract_customer_from_webhook(payload: dict[str, Any]) -> CustomerUpsert | None:
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            if not contacts:
                continue
            contact = contacts[0]
            profile = contact.get("profile", {})
            return CustomerUpsert(
                whatsapp_phone=contact.get("wa_id", ""),
                name=profile.get("name"),
            )
    return None


def extract_text_message(payload: dict[str, Any]) -> str | None:
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue
            message = messages[0]
            text = message.get("text", {})
            body = text.get("body")
            if body:
                return body
    return None


def extract_message_id(payload: dict[str, Any]) -> str | None:
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue
            message = messages[0]
            message_id = message.get("id")
            if message_id:
                return message_id
    return None


def extract_button_reply(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extrai resposta de botão interativo (QUICK_REPLY) do webhook WhatsApp.

    Returns:
        Dict com ``button_id``, ``button_title``, ``from_phone`` e ``message_id``,
        ou ``None`` se o payload não contiver uma resposta de botão.
    """
    entries = payload.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                if message.get("type") != "interactive":
                    continue
                interactive = message.get("interactive", {})
                if interactive.get("type") != "button_reply":
                    continue
                button_reply = interactive.get("button_reply", {})
                return {
                    "button_id": button_reply.get("id"),
                    "button_title": button_reply.get("title"),
                    "from_phone": message.get("from"),
                    "message_id": message.get("id"),
                }
    return None


async def send_whatsapp_message(to_phone: str, text: str) -> dict[str, Any]:
    """Envia mensagem de texto via WhatsApp Business API."""
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
                "Content-Type": "application/json",
            },
        )
        if resp.is_error:
            return {
                "error": "Meta API error",
                "status_code": resp.status_code,
                "response": resp.text,
            }
        return resp.json()


async def send_whatsapp_template(
    to_phone: str,
    template_name: str,
    language_code: str = "pt_BR",
    body_variables: list[str] | None = None,
) -> dict[str, Any]:
    """Envia uma mensagem via template aprovado pela Meta.

    Args:
        to_phone: Número de destino com DDI (ex: '5511999999999').
        template_name: Nome exato do template aprovado no Meta Business Manager.
        language_code: Código de idioma do template (padrão 'pt_BR').
        body_variables: Lista de valores para substituir {{1}}, {{2}}, ... no corpo.

    Returns:
        Resposta da API da Meta.
    """
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    components: list[dict[str, Any]] = []
    if body_variables:
        components.append({
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(v)}
                for v in body_variables
            ],
        })

    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components,
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
                "Content-Type": "application/json",
            },
        )
        if resp.is_error:
            return {
                "error": "Meta API error",
                "status_code": resp.status_code,
                "response": resp.text,
            }
        return resp.json()


async def send_whatsapp_image(to_phone: str, image_url: str, caption: str | None = None) -> dict[str, Any]:
    """Envia imagem por URL via WhatsApp Business API."""
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
    image_payload: dict[str, Any] = {"link": image_url}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "image",
        "image": image_payload,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
                "Content-Type": "application/json",
            },
        )
        if resp.is_error:
            return {
                "error": "Meta API error",
                "status_code": resp.status_code,
                "response": resp.text,
            }
        return resp.json()