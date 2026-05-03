from __future__ import annotations

from typing import Any

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