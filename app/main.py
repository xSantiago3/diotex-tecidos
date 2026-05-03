from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.repositories import get_order_for_customer, upsert_customer
from app.schemas import (
    AdminOtpResponse,
    AdminOtpStartRequest,
    AdminOtpVerifyRequest,
    AgentChatRequest,
    AgentChatResponse,
    CatalogListResponse,
    CheckoutQuoteRequest,
    CheckoutQuoteResponse,
    HealthResponse,
    OrderStatusResponse,
    ShippingQuoteRequest,
    ShippingQuoteResponse,
    WhatsAppWebhookPayload,
    WooSyncResponse,
)
from app.services.catalog import list_catalog_products
from app.services.checkout import create_checkout_quote
from app.agents.runtime import run_agent_message
from app.services.security import build_otp_code, start_admin_otp, verify_admin_otp
from app.services.shipping import calculate_shipping_quote
from app.services.whatsapp import extract_customer_from_webhook, extract_text_message
from app.services.woocommerce import sync_products_from_woocommerce


settings = get_settings()
SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", app_name=settings.app_name)


@app.get("/catalog/products", response_model=CatalogListResponse)
def list_catalog(
    session: SessionDep,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CatalogListResponse:
    return list_catalog_products(session=session, search=search, limit=limit, offset=offset)


@app.post("/admin/sync/woocommerce", response_model=WooSyncResponse)
async def sync_woocommerce_catalog(session: SessionDep) -> WooSyncResponse:
    try:
        imported_products, imported_images = await sync_products_from_woocommerce(session)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Falha no sync do WooCommerce: {exc}") from exc
    return WooSyncResponse(imported_products=imported_products, imported_images=imported_images)


@app.post("/shipping/quote", response_model=ShippingQuoteResponse)
async def shipping_quote(payload: ShippingQuoteRequest) -> ShippingQuoteResponse:
    try:
        return await calculate_shipping_quote(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Falha na cotacao de frete: {exc}") from exc


@app.post("/checkout/quote", response_model=CheckoutQuoteResponse)
async def checkout_quote(payload: CheckoutQuoteRequest, session: SessionDep) -> CheckoutQuoteResponse:
    return await create_checkout_quote(session, payload)


@app.post("/agent/chat", response_model=AgentChatResponse)
async def agent_chat(payload: AgentChatRequest) -> AgentChatResponse:
    response = await run_agent_message(payload.user_id, payload.session_id, payload.message)
    return AgentChatResponse(response=response)


@app.get("/customers/{whatsapp_phone}/orders/{order_id}", response_model=OrderStatusResponse)
def get_customer_order_status(whatsapp_phone: str, order_id: int, session: SessionDep) -> OrderStatusResponse:
    return get_order_for_customer(session, whatsapp_phone, order_id)


@app.post("/admin/otp/start", response_model=AdminOtpResponse)
def admin_otp_start(payload: AdminOtpStartRequest, session: SessionDep) -> AdminOtpResponse:
    challenge = start_admin_otp(session, payload.whatsapp_phone, payload.purpose)
    otp_code = build_otp_code(challenge.secret)
    return AdminOtpResponse(
        message=(
            "OTP gerado para o numero autorizado. "
            f"Codigo de desenvolvimento: {otp_code}. Em producao, entregue esse OTP pelo canal seguro configurado."
        ),
        expires_at=challenge.expires_at,
    )


@app.post("/admin/otp/verify", response_model=AdminOtpResponse)
def admin_otp_verify(payload: AdminOtpVerifyRequest, session: SessionDep) -> AdminOtpResponse:
    verify_admin_otp(session, payload.whatsapp_phone, payload.purpose, payload.otp_code)
    return AdminOtpResponse(message="OTP validado com sucesso.")


@app.get("/webhooks/whatsapp")
def verify_whatsapp_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    if not settings.meta_verify_token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="META_VERIFY_TOKEN nao configurado.")
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token and hub_challenge:
        return hub_challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Falha na verificacao do webhook.")


@app.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    customer_data = extract_customer_from_webhook(payload.payload)
    if customer_data and customer_data.whatsapp_phone:
        customer = upsert_customer(session, customer_data)
    else:
        customer = None

    message_text = extract_text_message(payload.payload)
    return {
        "received": True,
        "customer_id": customer.id if customer else None,
        "message_preview": message_text,
        "source_ip": request.client.host if request.client else None,
    }