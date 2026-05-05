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


def extract_incoming_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extrai a primeira mensagem recebida do webhook com metadados úteis."""
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue
            message = messages[0]
            message_type = message.get("type")
            base = {
                "type": message_type,
                "from_phone": message.get("from"),
                "message_id": message.get("id"),
            }
            if message_type == "text":
                text = message.get("text", {})
                return {**base, "text": text.get("body")}
            if message_type in {"image", "document"}:
                media = message.get(message_type, {})
                return {
                    **base,
                    "media_id": media.get("id"),
                    "mime_type": media.get("mime_type"),
                    "caption": media.get("caption"),
                    "filename": media.get("filename"),
                }
            return base
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


async def send_whatsapp_order_details_template(
    to_phone: str,
    template_name: str,
    language_code: str = "pt_BR",
    body_variables: list[str] | None = None,
    order_reference_id: str = "",
    order_item_name: str = "",
    total_amount_brl: float = 0.0,
    pix_key: str = "",
) -> dict[str, Any]:
    """Envia template ORDER_DETAILS com botão de pagamento PIX.

    Args:
        to_phone: Número de destino com DDI (ex: '5511999999999').
        template_name: Nome exato do template ORDER_DETAILS aprovado na Meta.
        language_code: Código de idioma do template (padrão 'pt_BR').
        body_variables: Variáveis para o corpo do template.
        order_reference_id: ID do pedido (reference_id no action).
        order_item_name: Nome/descrição do item exibido no resumo de pedido.
        total_amount_brl: Valor total em reais (ex: 150.00).
        pix_key: Chave PIX para o pagamento.

    Returns:
        Resposta da API da Meta.
    """
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    total_centavos = round(total_amount_brl * 100)

    components: list[dict[str, Any]] = []
    if body_variables:
        components.append({
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(v)}
                for v in body_variables
            ],
        })

    components.append({
        "type": "button",
        "sub_type": "order_details",
        "index": 0,
        "parameters": [
            {
                "type": "action",
                "action": {
                    "order_details": {
                        "reference_id": str(order_reference_id),
                        "type": "physical-goods",
                        "status": "pending",
                        "currency": "BRL",
                        "payment_settings": [
                            {
                                "type": "pix_static_code",
                            }
                        ],
                        "order": {
                            "status": "pending",
                            "items": [
                                {
                                    "name": (order_item_name[:60] if order_item_name else "Pedido"),
                                    "quantity": 1,
                                    "amount": {
                                        "offset": 100,
                                        "value": total_centavos,
                                    },
                                }
                            ],
                            "subtotal": {
                                "offset": 100,
                                "value": total_centavos,
                            },
                        },
                        "total_amount": {
                            "offset": 100,
                            "value": total_centavos,
                        },
                    }
                },
            }
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


async def get_whatsapp_media_url(media_id: str) -> dict[str, Any]:
    """Resolve URL temporária de uma mídia recebida via WhatsApp Business API."""
    settings = get_settings()
    if not settings.meta_whatsapp_access_token:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN nao configurado."}

    url = f"https://graph.facebook.com/v19.0/{media_id}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {settings.meta_whatsapp_access_token}"},
        )
        if resp.is_error:
            return {
                "error": "Meta API error",
                "status_code": resp.status_code,
                "response": resp.text,
            }
        return resp.json()


async def reupload_whatsapp_media(media_id: str, mime_type: str = "image/jpeg") -> dict[str, Any]:
    """Baixa uma mídia recebida e re-envia para a Media API da Meta, retornando novo media_id.

    A URL temporária de mídia recebida requer autenticação e não pode ser usada
    diretamente em mensagens de saída. Este helper faz o ciclo download → upload.
    """
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "Credenciais META não configuradas."}

    auth_header = {"Authorization": f"Bearer {settings.meta_whatsapp_access_token}"}

    # 1. Resolve a URL temporária autenticada
    meta_url = f"https://graph.facebook.com/v19.0/{media_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.get(meta_url, headers=auth_header)
        if meta_resp.is_error:
            return {"error": f"Falha ao resolver media_id: {meta_resp.status_code} {meta_resp.text[:200]}"}
        media_info = meta_resp.json()

    temp_url = media_info.get("url")
    mime_type = media_info.get("mime_type") or mime_type
    if not temp_url:
        return {"error": "URL temporária não encontrada na resposta da Meta."}

    # 2. Baixa os bytes da mídia
    async with httpx.AsyncClient(timeout=60) as client:
        download_resp = await client.get(temp_url, headers=auth_header)
        if download_resp.is_error:
            return {"error": f"Falha ao baixar mídia: {download_resp.status_code}"}
        media_bytes = download_resp.content

    # 3. Re-faz upload para a Media API da Meta
    upload_url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/media"
    ext = mime_type.split("/")[-1].split(";")[0] or "bin"
    filename = f"media.{ext}"
    async with httpx.AsyncClient(timeout=30) as client:
        upload_resp = await client.post(
            upload_url,
            headers=auth_header,
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, media_bytes, mime_type)},
        )
        if upload_resp.is_error:
            return {"error": f"Falha ao re-upar mídia: {upload_resp.status_code} {upload_resp.text[:200]}"}
        return upload_resp.json()  # contém {"id": "<novo_media_id>"}


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


async def send_whatsapp_image_by_id(to_phone: str, media_id: str, caption: str | None = None) -> dict[str, Any]:
    """Envia imagem usando media_id já upado na Meta (não usa URL externa)."""
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    image_payload: dict[str, Any] = {"id": media_id}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "image",
        "image": image_payload,
    }
    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
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
            return {"error": "Meta API error", "status_code": resp.status_code, "response": resp.text}
        return resp.json()


async def send_whatsapp_document_by_id(
    to_phone: str,
    media_id: str,
    filename: str,
    caption: str | None = None,
) -> dict[str, Any]:
    """Envia documento usando media_id já upado na Meta (não usa URL externa)."""
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    doc_payload: dict[str, Any] = {"id": media_id, "filename": filename}
    if caption:
        doc_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "document",
        "document": doc_payload,
    }
    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
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
            return {"error": "Meta API error", "status_code": resp.status_code, "response": resp.text}
        return resp.json()


async def send_whatsapp_document(
    to_phone: str,
    document_url: str,
    filename: str,
    caption: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.meta_whatsapp_access_token or not settings.meta_whatsapp_phone_number_id:
        return {"error": "META_WHATSAPP_ACCESS_TOKEN ou META_WHATSAPP_PHONE_NUMBER_ID nao configurados."}

    url = f"https://graph.facebook.com/v19.0/{settings.meta_whatsapp_phone_number_id}/messages"
    document_payload: dict[str, Any] = {
        "link": document_url,
        "filename": filename,
    }
    if caption:
        document_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "document",
        "document": document_payload,
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