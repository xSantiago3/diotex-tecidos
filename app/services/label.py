"""Geração automática de etiquetas de envio via Melhor Envio API.

Fluxo: add_to_cart → checkout → generate → print_url
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# User-Agent obrigatório pela ME API
_ME_USER_AGENT = "DiotexTecidos/1.0 (santiago-allan@hotmail.com)"


def _me_headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.melhor_envio_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _ME_USER_AGENT,
    }


def _me_url(path: str) -> str:
    settings = get_settings()
    base = settings.melhor_envio_base_url.rstrip("/")
    return f"{base}/api/v2{path}"


@dataclass
class LabelResult:
    shipment_id: str
    tracking_code: str | None
    label_url: str | None


async def _lookup_cep(cep: str) -> dict[str, str]:
    """Resolve CEP via ViaCEP. Retorna dict com logradouro, bairro, localidade, uf."""
    cep_digits = "".join(c for c in cep if c.isdigit())
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"https://viacep.com.br/ws/{cep_digits}/json/")
        resp.raise_for_status()
        data = resp.json()
    if data.get("erro"):
        raise ValueError(f"CEP {cep} não encontrado no ViaCEP.")
    return data


async def generate_label_for_order(
    order: Any,
    customer: Any,
    items: list[Any],
) -> LabelResult:
    """Executa o fluxo completo: carrinho → checkout → gerar → imprimir.

    Args:
        order: instância de Order com shipping_zipcode, shipping_quote_json, total_amount etc.
        customer: instância de Customer com name, email, whatsapp_phone, zipcode, address_number.
        items: lista de OrderItem.

    Returns:
        LabelResult com shipment_id, tracking_code e label_url (PDF público).
    """
    settings = get_settings()

    if not settings.melhor_envio_token:
        raise RuntimeError("MELHOR_ENVIO_TOKEN não configurado.")

    quote = order.shipping_quote_json or {}
    service_code = quote.get("service_code")
    if not service_code:
        raise RuntimeError("Pedido sem service_code na cotação — não é possível gerar etiqueta.")

    dest_cep = (order.shipping_zipcode or "").replace("-", "").strip()
    if not dest_cep:
        raise RuntimeError("Pedido sem CEP de destino.")

    # Resolve endereço destino via ViaCEP
    dest_addr = await _lookup_cep(dest_cep)

    # Dimensões do pacote salvas na cotação
    raw_quote = quote.get("raw") or {}
    packages = raw_quote.get("packages") or []
    if packages:
        pkg = packages[0]
        dims = pkg.get("dimensions", {})
        volume = {
            "height": int(dims.get("height") or 10),
            "width": int(dims.get("width") or 20),
            "length": int(dims.get("length") or 30),
            "weight": float(pkg.get("weight") or 1.0),
        }
    else:
        # Fallback conservador
        volume = {"height": 10, "width": 20, "length": 30, "weight": 1.0}

    products_payload = [
        {
            "name": i.product_name_snapshot[:50],
            "quantity": str(int(i.quantity)),
            "unitary_value": str(round(i.unit_price_snapshot, 2)),
        }
        for i in items
    ]

    phone_digits = "".join(c for c in (customer.whatsapp_phone or "") if c.isdigit())
    # Remove DDI 55 se presente
    if phone_digits.startswith("55") and len(phone_digits) > 11:
        phone_digits = phone_digits[2:]

    cart_body: dict[str, Any] = {
        "service": int(service_code),
        "from": {
            "name": settings.me_sender_name,
            "email": settings.me_sender_email,
            "phone": settings.me_sender_phone or phone_digits,
            "document": settings.me_sender_document,
            "state_register": "ISENTO",
            "address": settings.me_sender_address or "Endereço não configurado",
            "number": settings.me_sender_number or "S/N",
            "district": settings.me_sender_district or "Centro",
            "city": settings.me_sender_city or "São Paulo",
            "postal_code": "".join(c for c in settings.origin_zipcode if c.isdigit()),
            "state_abbr": settings.me_sender_state,
        },
        "to": {
            "name": customer.name or "Cliente",
            "email": customer.email or "",
            "phone": phone_digits,
            "document": "",
            "state_register": "ISENTO",
            "address": dest_addr.get("logradouro") or "Rua não informada",
            "complement": "",
            "number": customer.address_number or "S/N",
            "district": dest_addr.get("bairro") or "Centro",
            "city": dest_addr.get("localidade") or "",
            "postal_code": dest_cep,
            "country_id": "BR",
            "state_abbr": dest_addr.get("uf") or "",
        },
        "products": products_payload,
        "volumes": [volume],
        "options": {
            "insurance_value": round(order.total_amount, 2),
            "receipt": False,
            "own_hand": False,
            "reverse": False,
            "platform": "DiotexTecidos",
            "tags": [{"tag": str(order.id), "url": None}],
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Adicionar ao carrinho
        r = await client.post(_me_url("/me/cart"), headers=_me_headers(), json=cart_body)
        if r.is_error:
            raise RuntimeError(f"ME add_to_cart falhou ({r.status_code}): {r.text}")
        cart_item = r.json()
        shipment_id: str = cart_item.get("id") or cart_item.get("shipment_id") or str(cart_item)
        log.info("ME add_to_cart OK — shipment_id=%s pedido #%s", shipment_id, order.id)

        # 2. Checkout (pagar com saldo da conta)
        r = await client.post(
            _me_url("/me/shipment/checkout"),
            headers=_me_headers(),
            json={"orders": [shipment_id]},
        )
        if r.is_error:
            raise RuntimeError(f"ME checkout falhou ({r.status_code}): {r.text}")
        log.info("ME checkout OK — pedido #%s", order.id)

        # 3. Gerar etiqueta
        r = await client.post(
            _me_url("/me/shipment/generate"),
            headers=_me_headers(),
            json={"orders": [shipment_id]},
        )
        if r.is_error:
            raise RuntimeError(f"ME generate falhou ({r.status_code}): {r.text}")
        generated = r.json()
        log.info("ME generate OK — pedido #%s resp=%s", order.id, generated)

        # Extrai tracking_code da resposta (estrutura: {shipment_id: {tracking: ...}})
        tracking_code: str | None = None
        if isinstance(generated, dict):
            shipment_data = generated.get(shipment_id) or next(iter(generated.values()), {})
            if isinstance(shipment_data, dict):
                tracking_code = shipment_data.get("tracking")

        # 4. Imprimir (gerar link público PDF)
        r = await client.post(
            _me_url("/me/shipment/print"),
            headers=_me_headers(),
            json={"mode": "public", "orders": [shipment_id]},
        )
        label_url: str | None = None
        if not r.is_error:
            print_data = r.json()
            if isinstance(print_data, dict):
                label_url = print_data.get("url") or print_data.get("link")
            log.info("ME print OK — label_url=%s pedido #%s", label_url, order.id)
        else:
            log.warning("ME print falhou (%s): %s — continuando sem label_url", r.status_code, r.text)

    return LabelResult(
        shipment_id=shipment_id,
        tracking_code=tracking_code,
        label_url=label_url,
    )
