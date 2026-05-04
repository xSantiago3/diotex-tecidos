"""Integração com Mercado Pago para geração de PIX e confirmação de pagamento."""
from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

_MP_BASE = "https://api.mercadopago.com"


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.mercadopago_access_token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": "",  # será sobrescrito por chamada
    }


async def create_pix_payment(
    order_id: int,
    total_amount: float,
    customer_name: str,
    customer_email: str,
    customer_cpf: str | None = None,
    description: str = "Compra Diotex Tecidos",
) -> dict[str, Any]:
    """Cria um pagamento PIX no Mercado Pago e retorna QR code + copia-e-cola.

    Args:
        order_id: ID do pedido (usado como referência externa).
        total_amount: Valor total em reais.
        customer_name: Nome do comprador.
        customer_email: E-mail do comprador (obrigatório pelo MP).
        customer_cpf: CPF do comprador (opcional, melhora aprovação).
        description: Descrição que aparece no comprovante.

    Returns:
        Dict com 'qr_code' (copia-e-cola), 'qr_code_base64' e 'payment_id'.
    """
    settings = get_settings()
    if not settings.mercadopago_access_token:
        return {"error": "MERCADOPAGO_ACCESS_TOKEN nao configurado."}

    payer: dict[str, Any] = {
        "email": customer_email or "comprador@diotextecidos.com.br",
        "first_name": (customer_name or "Cliente").split()[0],
        "last_name": " ".join((customer_name or "Cliente").split()[1:]) or "Diotex",
    }
    if customer_cpf:
        payer["identification"] = {"type": "CPF", "number": customer_cpf}

    body = {
        "transaction_amount": round(float(total_amount), 2),
        "description": description,
        "payment_method_id": "pix",
        "payer": payer,
        "external_reference": str(order_id),
        "notification_url": f"https://diotex-tecidos-api-298996329023.us-central1.run.app/webhooks/mercadopago",
    }

    import uuid
    headers = _headers()
    headers["X-Idempotency-Key"] = f"order-{order_id}-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{_MP_BASE}/v1/payments", json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    txn = data.get("point_of_interaction", {}).get("transaction_data", {})
    return {
        "payment_id": data.get("id"),
        "status": data.get("status"),
        "qr_code": txn.get("qr_code"),          # copia-e-cola PIX
        "qr_code_base64": txn.get("qr_code_base64"),  # imagem QR (base64)
        "expires_at": data.get("date_of_expiration"),
    }


async def get_payment(payment_id: int | str) -> dict[str, Any]:
    """Consulta o status de um pagamento no Mercado Pago."""
    settings = get_settings()
    if not settings.mercadopago_access_token:
        return {"error": "MERCADOPAGO_ACCESS_TOKEN nao configurado."}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_MP_BASE}/v1/payments/{payment_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()
