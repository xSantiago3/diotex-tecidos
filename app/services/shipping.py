from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.schemas import ShippingProvider, ShippingQuoteOption, ShippingQuoteRequest, ShippingQuoteResponse

log = logging.getLogger(__name__)


def _sanitize_zipcode(zipcode: str) -> str:
    return "".join(ch for ch in zipcode if ch.isdigit())


async def _quote_melhor_envio(payload: ShippingQuoteRequest) -> list[ShippingQuoteOption]:
    settings = get_settings()
    if not settings.melhor_envio_token:
        raise ValueError("MELHOR_ENVIO_TOKEN nao configurado.")

    endpoint = f"{settings.melhor_envio_base_url.rstrip('/')}/api/v2/me/shipment/calculate"

    product_weight_kg = payload.weight_g / 1000.0
    declared_value = payload.insurance_value if payload.insurance_value is not None else payload.unit_price * payload.quantity

    request_body = {
        "from": {"postal_code": _sanitize_zipcode(settings.origin_zipcode)},
        "to": {"postal_code": _sanitize_zipcode(payload.to_zipcode)},
        "products": [
            {
                "id": "sku-1",
                "width": payload.package_width_cm,
                "height": payload.package_height_cm,
                "length": payload.package_length_cm,
                "weight": round(product_weight_kg, 3),
                "insurance_value": round(declared_value, 2),
                "quantity": payload.quantity,
            }
        ],
        "options": {"receipt": False, "own_hand": False},
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.melhor_envio_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "diotextecidos-agent-backend/0.1",
            },
            json=request_body,
        )
        if response.status_code == 401:
            log.error("Melhor Envio: token inválido ou expirado (401). Verifique MELHOR_ENVIO_TOKEN no Secret Manager.")
            return []
        response.raise_for_status()
        payload_response = response.json()

    options: list[ShippingQuoteOption] = []
    for item in payload_response if isinstance(payload_response, list) else []:
        if item.get("error"):
            continue
        price = item.get("price") or item.get("custom_price") or 0
        try:
            price_value = float(str(price).replace(",", "."))
        except ValueError:
            price_value = 0.0
        if price_value <= 0:
            continue

        delivery_days = item.get("delivery_time")
        try:
            delivery_days_value = int(delivery_days) if delivery_days is not None else None
        except (TypeError, ValueError):
            delivery_days_value = None

        options.append(
            ShippingQuoteOption(
                provider=ShippingProvider.melhor_envio,
                carrier_name=item.get("company", {}).get("name"),
                service_name=item.get("name") or "Servico",
                service_code=str(item.get("id")) if item.get("id") is not None else None,
                price=price_value,
                currency="BRL",
                delivery_days=delivery_days_value,
                delivery_days_with_preparation=(
                    delivery_days_value + settings.preparation_extra_days if delivery_days_value is not None else None
                ),
                raw=item,
            )
        )
    options.sort(key=lambda option: option.price)
    return options


async def _quote_mercado_livre(payload: ShippingQuoteRequest) -> list[ShippingQuoteOption]:
    settings = get_settings()
    if not settings.mercado_livre_access_token or not settings.mercado_livre_user_id:
        raise ValueError("Credenciais do Mercado Livre nao configuradas.")
    if not payload.mercado_livre_item_id:
        raise ValueError("Para cotacao via Mercado Livre, informe mercado_livre_item_id.")

    endpoint = f"{settings.mercado_livre_base_url.rstrip('/')}/items/{payload.mercado_livre_item_id}/shipping_options"
    params = {"zip_code": _sanitize_zipcode(payload.to_zipcode), "quantity": payload.quantity}

    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(
            endpoint,
            params=params,
            headers={
                "Authorization": f"Bearer {settings.mercado_livre_access_token}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload_response = response.json()

    options_payload = payload_response.get("options", []) if isinstance(payload_response, dict) else []
    options: list[ShippingQuoteOption] = []
    for item in options_payload:
        delivery_days = item.get("estimated_delivery_time", {}).get("shipping")
        list_cost = item.get("list_cost")
        if list_cost is None:
            list_cost = item.get("cost")
        if list_cost is None:
            list_cost = 0

        options.append(
            ShippingQuoteOption(
                provider=ShippingProvider.mercado_livre,
                carrier_name=item.get("shipping_company", {}).get("name") or "Mercado Envios",
                service_name=item.get("name") or "Mercado Envios",
                service_code=item.get("shipping_method_id"),
                price=float(list_cost),
                currency=payload_response.get("currency_id") or "BRL",
                delivery_days=int(delivery_days) if delivery_days is not None else None,
                delivery_days_with_preparation=(
                    int(delivery_days) + settings.preparation_extra_days if delivery_days is not None else None
                ),
                raw=item,
            )
        )
    return options


async def calculate_shipping_quote(payload: ShippingQuoteRequest) -> ShippingQuoteResponse:
    if payload.provider == ShippingProvider.melhor_envio:
        options = await _quote_melhor_envio(payload)
    elif payload.provider == ShippingProvider.mercado_livre:
        options = await _quote_mercado_livre(payload)
    else:
        options = []
    return ShippingQuoteResponse(options=options)