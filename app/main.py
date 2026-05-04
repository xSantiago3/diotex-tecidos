from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.logging_config import configure_logging

configure_logging()

from app.config import get_settings
from app.db import get_session, init_db
from app.repositories import get_order_for_customer, upsert_customer
from app.firestore_db import (
    get_order_firestore,
    update_order_status_firestore,
    update_order_firestore,
    get_order_items_firestore,
    create_order_firestore,
    upsert_customer_firestore,
)
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
from app.services.mercadopago import get_payment
from app.services.security import build_otp_code, start_admin_otp, verify_admin_otp
from app.services.shipping import calculate_shipping_quote
from app.services.label import generate_label_for_order
from app.services.whatsapp import (
    extract_button_reply,
    extract_customer_from_webhook,
    extract_message_id,
    extract_text_message,
    send_whatsapp_message,
    send_whatsapp_template,
)
from app.services.woocommerce import sync_products_from_woocommerce


settings = get_settings()
log = logging.getLogger(__name__)
SessionDep = Annotated[Session, Depends(get_session)]
_processed_message_ids: dict[str, float] = {}
_MESSAGE_DEDUP_TTL_SECONDS = 300.0
# admin_phone → order_id do último pedido PIX manual enviado para avaliação
_pending_pix_reviews: dict[str, int] = {}


def _admin_phones() -> list[str]:
    """Retorna lista de telefones dos administradores (dono + sócio, se configurado)."""
    phones = [settings.notification_phone]
    if settings.partner_phone and settings.partner_phone != settings.notification_phone:
        phones.append(settings.partner_phone)
    return phones


async def _send_separation_template_to_admins(
    order: Any,
    customer: Any,
    items: list[Any],
    label_url: str | None = None,
    tracking_code: str | None = None,
) -> None:
    """Envia template 'separar_pedido' para todos os admins após pagamento aprovado."""
    products_list = ", ".join(
        f"{i.product_name_snapshot} ({i.quantity}m)" for i in items
    )
    cep = getattr(order, "shipping_zipcode", None) or "não informado"
    address_num = getattr(customer, "address_number", None) or ""
    address_str = f"{cep}, nº {address_num}" if address_num else cep
    label_info = label_url or "a gerar"
    tracking_info = tracking_code or "a gerar"
    for phone in _admin_phones():
        result = await send_whatsapp_template(
            to_phone=phone,
            template_name=settings.order_separation_template_name,
            body_variables=[
                str(order.id),
                customer.name or "Sem nome",
                products_list,
                f"{order.total_amount:.2f}",
                address_str,
                label_info,
                tracking_info,
            ],
        )
        if isinstance(result, dict) and result.get("error"):
            log.error("Falha ao enviar separar_pedido para %s: %s", phone, result)


async def _send_pix_review_to_admins(
    order: Any,
    customer: Any,
    items: list[Any],
) -> None:
    """Envia template 'avaliar_pagamento_pix' (com botões confirmar/rejeitar) para admins."""
    products_list = ", ".join(
        f"{i.product_name_snapshot} ({i.quantity}m)" for i in items
    )
    for phone in _admin_phones():
        _pending_pix_reviews[phone] = order.id
        result = await send_whatsapp_template(
            to_phone=phone,
            template_name=settings.pix_review_template_name,
            body_variables=[
                str(order.id),
                customer.name or "Sem nome",
                customer.whatsapp_phone or "-",
                products_list,
                f"{order.total_amount:.2f}",
            ],
        )
        if isinstance(result, dict) and result.get("error"):
            log.error("Falha ao enviar avaliar_pagamento_pix para %s: %s", phone, result)


async def _handle_admin_button_reply(button_reply: dict[str, Any], session: Session) -> None:
    """Processa resposta de botão de admin para confirmar ou rejeitar pagamento PIX."""
    from sqlmodel import select as sql_select
    from app.models import Order, OrderItem, OrderStatus, Customer

    admin_phone = button_reply.get("from_phone")
    button_id = button_reply.get("button_id")

    if not admin_phone or button_id not in ("confirm_pix", "reject_pix"):
        return

    order_id = _pending_pix_reviews.get(admin_phone)
    if not order_id:
        await send_whatsapp_message(admin_phone, "⚠️ Nenhum pedido PIX pendente para avaliar.")
        return

    # Tenta Firestore primeiro se habilitado, senão fallback para SQLAlchemy
    if settings.firestore_enabled:
        try:
            order = await get_order_firestore(order_id)
            if not order:
                await send_whatsapp_message(admin_phone, f"⚠️ Pedido #{order_id} não encontrado.")
                _pending_pix_reviews.pop(admin_phone, None)
                return
            
            reviewable = ("payment_under_review", "awaiting_payment")
            if order.get("status") not in reviewable:
                await send_whatsapp_message(
                    admin_phone,
                    f"ℹ️ Pedido #{order_id} já foi processado (status: {order.get('status')}).",
                )
                _pending_pix_reviews.pop(admin_phone, None)
                return

            items = await get_order_items_firestore(order_id)
            customer_phone = order.get("customer_whatsapp")
            
            if button_id == "confirm_pix":
                await update_order_status_firestore(order_id, "paid")
                _pending_pix_reviews.pop(admin_phone, None)
                await send_whatsapp_message(admin_phone, f"✅ Pedido #{order_id} confirmado como pago!")
                log.info("Admin %s confirmou PIX do pedido #%s", admin_phone, order_id)
                
                if customer_phone:
                    shipping_quote = order.get("shipping_quote_json") or {}
                    delivery_days = shipping_quote.get("delivery_days_with_preparation")
                    delivery_str = f"{delivery_days} dias úteis" if delivery_days else "a combinar"
                    products_list = ", ".join(f"{i.get('product_name_snapshot', '')} ({i.get('quantity', 1)}m)" for i in items)
                    await send_whatsapp_template(
                        to_phone=customer_phone,
                        template_name=settings.order_confirmed_template_name,
                        body_variables=[
                            order.get("customer_name", "Cliente").split()[0],
                            str(order_id),
                            products_list,
                            f"{order.get('total_amount', 0):.2f}",
                            delivery_str,
                        ],
                    )
            elif button_id == "reject_pix":
                await update_order_status_firestore(order_id, "cancelled")
                _pending_pix_reviews.pop(admin_phone, None)
                await send_whatsapp_message(admin_phone, f"❌ Pedido #{order_id} rejeitado e cancelado.")
                log.info("Admin %s rejeitou PIX do pedido #%s", admin_phone, order_id)
                if customer_phone:
                    customer_name = order.get("customer_name", "")
                    await send_whatsapp_message(
                        customer_phone,
                        f"Olá{', ' + customer_name.split()[0] if customer_name else ''}! "
                        f"Infelizmente não conseguimos confirmar o pagamento do pedido #{order_id}. "
                        "Se você já realizou o pagamento, entre em contato conosco para verificarmos. 🙏",
                    )
        except Exception as exc:
            log.error("Erro ao processar button reply via Firestore: %s", exc)
            return

    # Fallback para SQLAlchemy se Firestore não disponível
    order = session.get(Order, order_id)
    if not order:
        await send_whatsapp_message(admin_phone, f"⚠️ Pedido #{order_id} não encontrado.")
        _pending_pix_reviews.pop(admin_phone, None)
        return

    reviewable = (OrderStatus.payment_under_review, OrderStatus.awaiting_payment)
    if order.status not in reviewable:
        await send_whatsapp_message(
            admin_phone,
            f"ℹ️ Pedido #{order_id} já foi processado (status: {order.status.value}).",
        )
        _pending_pix_reviews.pop(admin_phone, None)
        return

    customer = session.get(Customer, order.customer_id)
    items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()

    if button_id == "confirm_pix":
        order.status = OrderStatus.paid
        session.add(order)
        session.commit()
        _pending_pix_reviews.pop(admin_phone, None)
        await send_whatsapp_message(admin_phone, f"✅ Pedido #{order_id} confirmado como pago!")
        log.info("Admin %s confirmou PIX do pedido #%s", admin_phone, order_id)
        # Notifica cliente com template de confirmação
        if customer and customer.whatsapp_phone:
            shipping_quote = getattr(order, "shipping_quote_json", None) or {}
            delivery_days = shipping_quote.get("delivery_days_with_preparation")
            delivery_str = f"{delivery_days} dias úteis" if delivery_days else "a combinar"
            products_list = ", ".join(f"{i.product_name_snapshot} ({i.quantity}m)" for i in items)
            await send_whatsapp_template(
                to_phone=customer.whatsapp_phone,
                template_name=settings.order_confirmed_template_name,
                body_variables=[
                    (customer.name or "Cliente").split()[0],
                    str(order.id),
                    products_list,
                    f"{order.total_amount:.2f}",
                    delivery_str,
                ],
            )
    elif button_id == "reject_pix":
        order.status = OrderStatus.cancelled
        session.add(order)
        session.commit()
        _pending_pix_reviews.pop(admin_phone, None)
        await send_whatsapp_message(admin_phone, f"❌ Pedido #{order_id} rejeitado e cancelado.")
        log.info("Admin %s rejeitou PIX do pedido #%s", admin_phone, order_id)
        if customer and customer.whatsapp_phone:
            await send_whatsapp_message(
                customer.whatsapp_phone,
                f"Olá{', ' + customer.name.split()[0] if customer.name else ''}! "
                f"Infelizmente não conseguimos confirmar o pagamento do pedido #{order_id}. "
                "Se você já realizou o pagamento, entre em contato conosco para verificarmos. 🙏",
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Auto-sincroniza catálogo WooCommerce na inicialização do container
    if settings.woocommerce_base_url and settings.woocommerce_consumer_key and settings.woocommerce_consumer_secret:
        try:
            from app.db import get_session as _get_session
            with next(_get_session()) as _session:
                imported, images = await sync_products_from_woocommerce(_session)
            log.info("Auto-sync WooCommerce: %d produtos, %d imagens importados.", imported, images)
        except Exception as _exc:
            log.error("Falha no auto-sync WooCommerce na inicializacao: %s", _exc)
    else:
        log.warning("Credenciais WooCommerce nao configuradas; auto-sync ignorado.")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


def _prune_processed_message_ids(now: float) -> None:
    stale_ids = [
        message_id
        for message_id, ts in _processed_message_ids.items()
        if (now - ts) > _MESSAGE_DEDUP_TTL_SECONDS
    ]
    for message_id in stale_ids:
        _processed_message_ids.pop(message_id, None)


def _mark_message_processed(message_id: str) -> bool:
    now = time.time()
    _prune_processed_message_ids(now)
    if message_id in _processed_message_ids:
        return False
    _processed_message_ids[message_id] = now
    return True


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


@app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    if not settings.meta_verify_token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="META_VERIFY_TOKEN nao configurado.")
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token and hub_challenge:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Falha na verificacao do webhook.")


@app.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    raw = payload.model_dump()
    message_id = extract_message_id(raw)
    if message_id and not _mark_message_processed(message_id):
        return {"received": True, "duplicate": True}

    # Resposta de botão interativo (confirmar/rejeitar PIX)
    button_reply = extract_button_reply(raw)
    if button_reply:
        await _handle_admin_button_reply(button_reply, session)
        return {"received": True}

    customer_data = extract_customer_from_webhook(raw)
    if customer_data and customer_data.whatsapp_phone:
        customer = upsert_customer(session, customer_data)
    else:
        customer = None

    message_text = extract_text_message(raw)

    if message_text and customer and customer.whatsapp_phone:
        try:
            orchestrator_message = (
                "[INTERNAL_CONTEXT]\n"
                f"customer_whatsapp_phone={customer.whatsapp_phone}\n"
                "[/INTERNAL_CONTEXT]\n"
                f"Mensagem do cliente: {message_text}"
            )

            # Resposta principal sempre vem do ADK/LLM.
            agent_response = await run_agent_message(
                user_id=customer.whatsapp_phone,
                session_id=customer.whatsapp_phone,
                message=orchestrator_message,
            )

            send_result = await send_whatsapp_message(to_phone=customer.whatsapp_phone, text=agent_response)
            if isinstance(send_result, dict) and send_result.get("error"):
                log.error(
                    "Falha ao enviar mensagem WhatsApp",
                    extra={
                        "event": "whatsapp_send_failed",
                        "status_code": send_result.get("status_code"),
                        "response": send_result.get("response"),
                        "customer_phone": customer.whatsapp_phone,
                    },
                )
        except Exception as exc:
            log.exception(
                "Erro ao processar mensagem do agente",
                extra={
                    "event": "agent_message_processing_failed",
                    "customer_phone": customer.whatsapp_phone,
                },
            )

    return {"received": True}


@app.post("/internal/orders/{order_id}/notify-pix-pending")
async def notify_pix_pending(order_id: int, session: SessionDep) -> dict[str, Any]:
    """Envia notificações de PIX manual pendente: template de espera ao cliente e
    template de avaliação (com botões confirmar/rejeitar) para os admins.

    Deve ser chamado quando o agente registrar um pedido com pagamento via PIX manual.
    """
    from sqlmodel import select as sql_select
    from app.models import Order, OrderItem, OrderStatus, Customer
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Pedido #{order_id} não encontrado.")

    customer = session.get(Customer, order.customer_id)
    items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()

    # Garante status correto
    if order.status == OrderStatus.draft:
        order.status = OrderStatus.payment_under_review
        session.add(order)
        session.commit()

    products_list = ", ".join(f"{i.product_name_snapshot} ({i.quantity}m)" for i in items)

    # Template para o cliente: aguardando confirmação de pagamento
    if customer and customer.whatsapp_phone:
        client_result = await send_whatsapp_template(
            to_phone=customer.whatsapp_phone,
            template_name=settings.pix_awaiting_template_name,
            body_variables=[
                (customer.name or "Cliente").split()[0],  # {{1}} = primeiro nome
                str(order.id),                             # {{2}} = número do pedido
                products_list,                             # {{3}} = produtos
                f"{order.total_amount:.2f}",               # {{4}} = total
                settings.pix_key or "a combinar",          # {{5}} = chave PIX
            ],
        )
        if isinstance(client_result, dict) and client_result.get("error"):
            log.error("Falha ao enviar pedido_aguardando_pix para %s: %s", customer.whatsapp_phone, client_result)

    # Template com botões para admins avaliarem o pagamento
    await _send_pix_review_to_admins(order, customer, items)
    log.info("Notificacoes de PIX pendente enviadas para pedido #%s", order_id)
    return {"notified": True, "order_id": order_id}


@app.post("/webhooks/mercadopago")
async def mercadopago_webhook(request: Request, session: SessionDep) -> dict[str, Any]:
    """Recebe notificações de pagamento do Mercado Pago."""
    import logging
    from sqlmodel import select as sql_select
    from app.models import Order, OrderItem, OrderStatus, Customer

    log = logging.getLogger(__name__)
    try:
        body = await request.json()
    except Exception:
        return {"received": True}

    # MP envia {"type": "payment", "data": {"id": "123456"}}
    if body.get("type") != "payment":
        return {"received": True}

    payment_id = str(body.get("data", {}).get("id", ""))
    if not payment_id:
        return {"received": True}

    try:
        payment_data = await get_payment(payment_id)
        mp_status = payment_data.get("status")
        external_ref = payment_data.get("external_reference")  # order_id

        if mp_status != "approved" or not external_ref:
            return {"received": True}

        order_id = int(external_ref)

        # Tenta Firestore primeiro se habilitado
        if settings.firestore_enabled:
            try:
                order = await get_order_firestore(order_id)
                if not order or order.get("status") == "paid":
                    return {"received": True}

                # Atualiza pedido para pago com referência de pagamento
                await update_order_firestore(
                    order_id,
                    {
                        "status": "paid",
                        "payment_reference": payment_id,
                        "updated_at": __import__("datetime").datetime.utcnow().isoformat(),
                    }
                )

                # Busca items
                items = await get_order_items_firestore(order_id)

                # Monta mensagem de notificação para o admin
                items_text = "\n".join(
                    f"  • {i.get('product_name_snapshot', '')} — {i.get('quantity', 1)} {'' if i.get('quantity', 1) == 1 else 'unid.'} × R${i.get('unit_price_snapshot', 0):.2f} = R${i.get('line_total', 0):.2f}"
                    for i in items
                )
                customer_phone = order.get("customer_whatsapp")
                customer_name = order.get("customer_name", "Sem nome")
                address_info = f"\nNúmero: {order.get('address_number')}" if order.get("address_number") else ""
                zipcode_info = f"\nCEP: {order.get('shipping_zipcode')}" if order.get("shipping_zipcode") else ""

                msg = (
                    f"✅ *PAGAMENTO APROVADO*\n"
                    f"Pedido #{order_id}\n"
                    f"\n👤 *Cliente:* {customer_name}\n"
                    f"📱 *Telefone:* {customer_phone or '-'}"
                    f"{address_info}{zipcode_info}\n"
                    f"\n📦 *Itens:*\n{items_text}\n"
                    f"\n💰 Subtotal: R${order.get('subtotal_amount', 0):.2f}"
                    f"\n🚚 Frete: R${order.get('shipping_amount', 0):.2f}"
                    f"\n💵 *Total: R${order.get('total_amount', 0):.2f}*"
                )

                await send_whatsapp_message(to_phone=settings.notification_phone, text=msg)
                log.info("Notificacao de pagamento enviada para %s — pedido #%s", settings.notification_phone, order_id)

                # Gera etiqueta no Melhor Envio automaticamente
                label_url: str | None = None
                tracking_code: str | None = None
                shipping_quote = order.get("shipping_quote_json") or {}
                if settings.melhor_envio_token and shipping_quote.get("service_code"):
                    try:
                        # Precisa de um objeto Order fake para generate_label_for_order
                        class FakeOrder:
                            pass
                        fake_order = FakeOrder()
                        for k, v in order.items():
                            setattr(fake_order, k, v)

                        label_result = await generate_label_for_order(fake_order, order, items)
                        
                        await update_order_firestore(
                            order_id,
                            {
                                "me_shipment_id": label_result.shipment_id,
                                "tracking_code": label_result.tracking_code,
                                "label_url": label_result.label_url,
                            }
                        )
                        label_url = label_result.label_url
                        tracking_code = label_result.tracking_code
                        log.info(
                            "Etiqueta ME gerada — pedido #%s shipment=%s tracking=%s",
                            order_id, label_result.shipment_id, tracking_code,
                        )
                    except Exception as label_exc:
                        log.error("Falha ao gerar etiqueta ME para pedido #%s: %s", order_id, label_exc)
                        await send_whatsapp_message(
                            to_phone=settings.notification_phone,
                            text=(
                                f"⚠️ *Falha ao gerar etiqueta automática*\n"
                                f"Pedido #{order_id}\nMotivo: {label_exc}\n"
                                "Gere manualmente no painel do Melhor Envio."
                            ),
                        )

                # Avisa admins para separar o pedido
                await _send_separation_template_to_admins(
                    order, order, items,
                    label_url=label_url,
                    tracking_code=tracking_code,
                )

                # Envia template de confirmação para o cliente
                if customer_phone:
                    delivery_days = shipping_quote.get("delivery_days_with_preparation")
                    delivery_str = f"{delivery_days} dias uteis" if delivery_days else "a combinar"

                    products_list = ", ".join(
                        f"{i.get('product_name_snapshot', '')} ({i.get('quantity', 1)}m)"
                        for i in items
                    )

                    template_result = await send_whatsapp_template(
                        to_phone=customer_phone,
                        template_name=settings.order_confirmed_template_name,
                        language_code="pt_BR",
                        body_variables=[
                            (customer_name or "Cliente").split()[0],
                            str(order_id),
                            products_list,
                            f"{order.get('total_amount', 0):.2f}",
                            delivery_str,
                        ],
                    )
                    if isinstance(template_result, dict) and template_result.get("error"):
                        log.error(
                            "Falha ao enviar template de confirmacao para %s: %s",
                            customer_phone, template_result,
                        )
                    else:
                        log.info("Template pedido_confirmado enviado para %s", customer_phone)

                return {"received": True}
            except Exception as fs_exc:
                log.error("Erro ao processar webhook MP via Firestore: %s", fs_exc)
                # Continua com fallback SQLAlchemy

        # Fallback para SQLAlchemy
        order = session.exec(
            sql_select(Order).where(Order.id == order_id)
        ).first()
        if not order or order.status == OrderStatus.paid:
            return {"received": True}

        # Atualiza pedido para pago
        order.status = OrderStatus.paid
        order.payment_reference = payment_id
        session.add(order)
        session.commit()

        # Busca dados do cliente e itens
        customer = session.get(Customer, order.customer_id)
        items = session.exec(
            sql_select(OrderItem).where(OrderItem.order_id == order.id)
        ).all()

        # Monta mensagem de notificação para o admin
        items_text = "\n".join(
            f"  • {i.product_name_snapshot} — {i.quantity} {'' if i.quantity == 1 else 'unid.'} × R${i.unit_price_snapshot:.2f} = R${i.line_total:.2f}"
            for i in items
        )
        address_info = f"\nNúmero: {customer.address_number}" if customer and customer.address_number else ""
        zipcode_info = f"\nCEP: {order.shipping_zipcode}" if order.shipping_zipcode else ""

        msg = (
            f"✅ *PAGAMENTO APROVADO*\n"
            f"Pedido #{order.id}\n"
            f"\n👤 *Cliente:* {customer.name or 'Sem nome'}\n"
            f"📱 *Telefone:* {customer.whatsapp_phone if customer else '-'}"
            f"{address_info}{zipcode_info}\n"
            f"\n📦 *Itens:*\n{items_text}\n"
            f"\n💰 Subtotal: R${order.subtotal_amount:.2f}"
            f"\n🚚 Frete: R${order.shipping_amount:.2f}"
            f"\n💵 *Total: R${order.total_amount:.2f}*"
        )

        await send_whatsapp_message(to_phone=settings.notification_phone, text=msg)
        log.info("Notificacao de pagamento enviada para %s — pedido #%s", settings.notification_phone, order.id)

        # Gera etiqueta no Melhor Envio automaticamente
        label_url: str | None = None
        tracking_code: str | None = None
        if settings.melhor_envio_token and order.shipping_quote_json and order.shipping_quote_json.get("service_code"):
            try:
                label_result = await generate_label_for_order(order, customer, items)
                order.me_shipment_id = label_result.shipment_id
                order.tracking_code = label_result.tracking_code
                order.label_url = label_result.label_url
                session.add(order)
                session.commit()
                label_url = label_result.label_url
                tracking_code = label_result.tracking_code
                log.info(
                    "Etiqueta ME gerada — pedido #%s shipment=%s tracking=%s",
                    order.id, label_result.shipment_id, tracking_code,
                )
            except Exception as label_exc:
                log.error("Falha ao gerar etiqueta ME para pedido #%s: %s", order.id, label_exc)
                await send_whatsapp_message(
                    to_phone=settings.notification_phone,
                    text=(
                        f"⚠️ *Falha ao gerar etiqueta automática*\n"
                        f"Pedido #{order.id}\nMotivo: {label_exc}\n"
                        "Gere manualmente no painel do Melhor Envio."
                    ),
                )

        # Avisa admins para separar o pedido (com link da etiqueta se disponível)
        await _send_separation_template_to_admins(
            order, customer, items,
            label_url=label_url,
            tracking_code=tracking_code,
        )

        # Envia template de confirmação para o cliente
        if customer and customer.whatsapp_phone:
            shipping_quote = order.shipping_quote_json or {}
            delivery_days = shipping_quote.get("delivery_days_with_preparation")
            delivery_str = f"{delivery_days} dias uteis" if delivery_days else "a combinar"

            products_list = ", ".join(
                f"{i.product_name_snapshot} ({i.quantity}m)"
                for i in items
            )

            template_result = await send_whatsapp_template(
                to_phone=customer.whatsapp_phone,
                template_name=settings.order_confirmed_template_name,
                language_code="pt_BR",
                body_variables=[
                    (customer.name or "Cliente").split()[0],  # {{1}} = primeiro nome
                    str(order.id),                             # {{2}} = número do pedido
                    products_list,                             # {{3}} = produtos
                    f"{order.total_amount:.2f}",               # {{4}} = valor total
                    delivery_str,                              # {{5}} = prazo de entrega
                ],
            )
            if isinstance(template_result, dict) and template_result.get("error"):
                log.error(
                    "Falha ao enviar template de confirmacao para %s: %s",
                    customer.whatsapp_phone, template_result,
                )
            else:
                log.info("Template pedido_confirmado enviado para %s", customer.whatsapp_phone)

    except Exception as exc:
        log.error("Erro ao processar webhook Mercado Pago: %s", exc)

    return {"received": True}