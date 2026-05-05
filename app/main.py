from __future__ import annotations

import asyncio
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
    delete_customer_data_firestore,
    delete_order_firestore,
    get_customer_firestore,
    get_latest_pending_pix_order_firestore,
    get_order_firestore,
    list_expired_open_orders_firestore,
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
from app.agents.runtime import run_agent_message, reset_session
from app.services.mercadopago import get_payment
from app.services.security import build_otp_code, start_admin_otp, verify_admin_otp
from app.services.shipping import calculate_shipping_quote
from app.services.label import generate_label_for_order
from app.services.whatsapp import (
    extract_incoming_message,
    extract_button_reply,
    extract_customer_from_webhook,
    extract_message_id,
    extract_text_message,
    get_whatsapp_media_url,
    reupload_whatsapp_media,
    send_whatsapp_document,
    send_whatsapp_document_by_id,
    send_whatsapp_image,
    send_whatsapp_image_by_id,
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
# Lock por usuário: evita processamento concorrente de mensagens do mesmo número
_user_locks: dict[str, asyncio.Lock] = {}
_CHECKOUT_STUCK_HINTS: tuple[str, ...] = (
    "carrinho vazio",
    "contexto do carrinho ausente",
    "primeiro chame prepare_checkout_options",
    "shipping_option_index invalido",
)


def _admin_phones() -> list[str]:
    """Retorna lista de telefones dos administradores (dono + sócio, se configurado)."""
    phones = [settings.notification_phone]
    if settings.partner_phone and settings.partner_phone != settings.notification_phone:
        phones.append(settings.partner_phone)
    return phones


def _validate_scheduler_token(token: str | None) -> None:
    """Valida token de autenticação para endpoints internos de manutenção."""
    if not settings.scheduler_token:
        raise HTTPException(
            status_code=500,
            detail="Scheduler token não configurado no servidor."
        )
    if not token or token != settings.scheduler_token:
        raise HTTPException(
            status_code=401,
            detail="Token de autenticação inválido ou ausente."
        )


def _field_val(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _coerce_order_object(order: Any) -> Any:
    if not isinstance(order, dict):
        return order

    class FakeOrder:
        pass

    fake_order = FakeOrder()
    for key, value in order.items():
        setattr(fake_order, key, value)
    return fake_order


def _build_orchestrator_message(customer_phone: str, message_text: str, is_admin: bool) -> str:
    return (
        "[INTERNAL_CONTEXT]\n"
        f"customer_whatsapp_phone={customer_phone}\n"
        f"is_admin={'true' if is_admin else 'false'}\n"
        "[/INTERNAL_CONTEXT]\n"
        f"Mensagem do cliente: {message_text}"
    )


def _looks_like_checkout_stuck(agent_response: str) -> bool:
    normalized = (agent_response or "").lower()
    return any(hint in normalized for hint in _CHECKOUT_STUCK_HINTS)


async def _run_orchestrator_with_recovery(
    *,
    customer_phone: str,
    message_text: str,
    is_admin: bool,
    session_id: str,
) -> str:
    orchestrator_message = _build_orchestrator_message(customer_phone, message_text, is_admin)
    agent_response = await run_agent_message(
        user_id=customer_phone,
        session_id=session_id,
        message=orchestrator_message,
    )

    if is_admin or not _looks_like_checkout_stuck(agent_response):
        return agent_response

    # Recupera sessão quando o fluxo ficou preso em checkout/pagamento antigo.
    log.warning(
        "Checkout session appears stuck; resetting and retrying once",
        extra={
            "event": "checkout_session_recovery",
            "customer_phone": customer_phone,
            "agent_response": agent_response,
        },
    )
    await reset_session(user_id=customer_phone, session_id=session_id)
    return await run_agent_message(
        user_id=customer_phone,
        session_id=session_id,
        message=orchestrator_message,
    )


async def _notify_label_failure_to_admins(order_id: int, label_error_text: str) -> None:
    for phone in _admin_phones():
        await send_whatsapp_message(
            to_phone=phone,
            text=(
                f"⚠️ *Falha ao gerar etiqueta automática*\n"
                f"Pedido #{order_id}\nMotivo: {label_error_text}\n"
                "Gere manualmente no painel do Melhor Envio."
            ),
        )


async def _generate_label_and_notify_admins(
    order: Any,
    customer: Any,
    items: list[Any],
    *,
    session: Session | None = None,
) -> None:
    order_id = int(_field_val(order, "id", 0) or 0)
    shipping_quote = _field_val(order, "shipping_quote_json", {}) or {}
    label_url = _field_val(order, "label_url")
    tracking_code = _field_val(order, "tracking_code")

    if settings.melhor_envio_token and shipping_quote.get("service_code"):
        try:
            label_result = await generate_label_for_order(
                _coerce_order_object(order),
                customer or order,
                items,
            )
            # Sandbox do ME pode não retornar tracking no generate/tracking.
            # Para manter a comunicação consistente com o cliente, usa fallback conhecido.
            resolved_tracking_code = label_result.tracking_code
            if (
                not resolved_tracking_code
                and "sandbox" in (settings.melhor_envio_base_url or "").lower()
            ):
                resolved_tracking_code = "460124364"

            if isinstance(order, dict):
                await update_order_firestore(
                    order_id,
                    {
                        "me_shipment_id": label_result.shipment_id,
                        "tracking_code": resolved_tracking_code,
                        "label_url": label_result.label_url,
                    },
                )
                order["me_shipment_id"] = label_result.shipment_id
                order["tracking_code"] = resolved_tracking_code
                order["label_url"] = label_result.label_url
            else:
                order.me_shipment_id = label_result.shipment_id
                order.tracking_code = resolved_tracking_code
                order.label_url = label_result.label_url
                if session is not None:
                    session.add(order)
                    session.commit()
            label_url = label_result.label_url
            tracking_code = resolved_tracking_code
            log.info(
                "Etiqueta ME gerada — pedido #%s shipment=%s tracking=%s",
                order_id,
                label_result.shipment_id,
                tracking_code,
            )
        except Exception as label_exc:
            label_error_text = str(label_exc)
            if "from.document" in label_error_text:
                label_error_text = (
                    "CPF/CNPJ do remetente invalido no Melhor Envio "
                    "(configuracao ME_SENDER_DOCUMENT)."
                )
            log.error("Falha ao gerar etiqueta ME para pedido #%s: %s", order_id, label_exc)
            await _notify_label_failure_to_admins(order_id, label_error_text)

    await _send_separation_template_to_admins(
        order,
        customer or order,
        items,
        label_url=label_url,
        tracking_code=tracking_code,
    )


async def _send_customer_tracking_update(order: Any, customer: Any, items: list[Any]) -> None:
    customer_phone = _field_val(customer, "whatsapp_phone") or _field_val(order, "customer_whatsapp")
    if not customer_phone:
        return

    tracking_code = _field_val(order, "tracking_code")
    if (
        not tracking_code
        and "sandbox" in (settings.melhor_envio_base_url or "").lower()
    ):
        tracking_code = "460124364"
    if not tracking_code:
        return

    if not tracking_code:
        return

    products_list = ", ".join(
        f"{_field_val(i, 'product_name_snapshot', '')} ({_field_val(i, 'quantity', 1)}m)" for i in items
    )
    message_lines = [
        "Pagamento aprovado com sucesso! Agora vamos iniciar a separação para envio. 📦",
        "",
        f"Pedido #{_field_val(order, 'id', '')}",
        f"Itens: {products_list}",
        f"Total: R${float(_field_val(order, 'total_amount', 0) or 0):.2f}",
        "",
        f"Código de rastreio: *{tracking_code}*",
        f"Acompanhe seu pedido em: https://melhorrastreio.com.br/rastreio/{tracking_code}",
    ]

    await send_whatsapp_message(to_phone=customer_phone, text="\n".join(message_lines))


def _get_latest_pending_pix_order_sql(session: Session, customer_phone: str) -> Any | None:
    from sqlmodel import select as sql_select
    from app.models import Customer, Order, OrderStatus

    customer = session.exec(
        sql_select(Customer).where(Customer.whatsapp_phone == customer_phone)
    ).first()
    if not customer:
        return None

    return session.exec(
        sql_select(Order)
        .where(
            Order.customer_id == customer.id,
            Order.payment_provider == "pix_manual",
            Order.status == OrderStatus.payment_under_review,
        )
        .order_by(Order.updated_at.desc())
    ).first()


async def _forward_pix_receipt_to_admins(
    incoming_message: dict[str, Any],
    order: Any,
    customer: Any,
    items: list[Any],
) -> None:
    order_id = int(_field_val(order, "id", 0) or 0)
    products_list = ", ".join(
        f"{_field_val(i, 'product_name_snapshot', '')} ({_field_val(i, 'quantity', 1)}m)" for i in items
    )
    pix_details = (
        f"📌 *Comprovante PIX recebido*\n"
        f"Pedido #{order_id}\n"
        f"Cliente: {_field_val(customer, 'name') or _field_val(order, 'customer_name') or 'Sem nome'}\n"
        f"Telefone: {_field_val(customer, 'whatsapp_phone') or _field_val(order, 'customer_whatsapp') or '-'}\n"
        f"Produtos: {products_list}\n"
        f"Total: R${float(_field_val(order, 'total_amount', 0) or 0):.2f}\n"
        f"Chave PIX: {settings.pix_key or 'não configurada'}"
    )

    message_type = incoming_message.get("type")
    caption = incoming_message.get("caption")
    media_id = incoming_message.get("media_id")

    # Re-upload da mídia: a URL temporária da Meta requer auth e não pode ser usada
    # diretamente em mensagens de saída. Precisamos baixar e re-upar para obter novo media_id.
    new_media_id: str | None = None
    if media_id:
        reupload_result = await reupload_whatsapp_media(media_id)
        if reupload_result.get("id"):
            new_media_id = reupload_result["id"]
        else:
            log.error(
                "Falha ao re-upar midia do comprovante PIX",
                extra={
                    "event": "pix_receipt_reupload_error",
                    "order_id": order_id,
                    "media_id": media_id,
                    "result": reupload_result,
                },
            )

    for phone in _admin_phones():
        await send_whatsapp_message(to_phone=phone, text=pix_details)
        if caption:
            await send_whatsapp_message(to_phone=phone, text=f"Legenda do comprovante: {caption}")
        if media_id and not new_media_id:
            await send_whatsapp_message(
                to_phone=phone,
                text=f"⚠️ Imagem do comprovante recebida mas não foi possível encaminhar (erro no re-upload). media_id original: {media_id}",
            )
        elif new_media_id:
            if message_type == "image":
                await send_whatsapp_image_by_id(
                    to_phone=phone,
                    media_id=new_media_id,
                    caption=f"Comprovante do pedido #{order_id}",
                )
            elif message_type == "document":
                await send_whatsapp_document_by_id(
                    to_phone=phone,
                    media_id=new_media_id,
                    filename=incoming_message.get("filename") or f"comprovante-pedido-{order_id}",
                    caption=f"Comprovante do pedido #{order_id}",
                )

    await _send_pix_review_to_admins(order, customer, items)


async def _maybe_handle_pix_receipt(
    incoming_message: dict[str, Any] | None,
    customer: Any,
    session: Session,
) -> bool:
    if not incoming_message or incoming_message.get("type") not in {"image", "document"}:
        return False

    customer_phone = getattr(customer, "whatsapp_phone", None)
    if not customer_phone:
        return False

    if settings.firestore_enabled:
        order = await get_latest_pending_pix_order_firestore(customer_phone)
        if not order:
            return False
        items = await get_order_items_firestore(int(order.get("id") or 0))
        customer_doc = await get_customer_firestore(customer_phone)
        await _forward_pix_receipt_to_admins(incoming_message, order, customer_doc or order, items)
    else:
        order = _get_latest_pending_pix_order_sql(session, customer_phone)
        if not order:
            return False
        from sqlmodel import select as sql_select
        from app.models import OrderItem

        items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()
        await _forward_pix_receipt_to_admins(incoming_message, order, customer, items)

    await send_whatsapp_message(
        to_phone=customer_phone,
        text=(
            "Recebemos seu comprovante e já enviamos para conferência. "
            "Assim que o pagamento for confirmado, enviaremos os dados do pedido e o rastreio."
        ),
    )
    return True


async def _send_separation_template_to_admins(
    order: Any,
    customer: Any,
    items: list[Any],
    label_url: str | None = None,
    tracking_code: str | None = None,
) -> None:
    """Envia template 'separar_pedido' para todos os admins após pagamento aprovado."""
    products_list = ", ".join(
        f"{_field_val(i, 'product_name_snapshot', '')} ({_field_val(i, 'quantity', 1)}m)" for i in items
    )
    cep = _field_val(order, "shipping_zipcode") or "não informado"
    address_num = _field_val(customer, "address_number") or _field_val(order, "address_number") or ""
    address_str = f"{cep}, nº {address_num}" if address_num else cep
    label_info = label_url or "a gerar"
    tracking_info = tracking_code or "a gerar"
    for phone in _admin_phones():
        result = await send_whatsapp_template(
            to_phone=phone,
            template_name=settings.order_separation_template_name,
            body_variables=[
                str(_field_val(order, "id", "")),
                _field_val(customer, "name") or _field_val(order, "customer_name") or "Sem nome",
                products_list,
                f"{float(_field_val(order, 'total_amount', 0) or 0):.2f}",
                address_str,
                label_info,
                tracking_info,
            ],
        )
        if isinstance(result, dict) and result.get("error"):
            log.error("Falha ao enviar separar_pedido para %s: %s", phone, result)
            fallback_lines = [
                "Pedido pronto para separação.",
                f"Pedido #{_field_val(order, 'id', '')}",
                f"Cliente: {_field_val(customer, 'name') or _field_val(order, 'customer_name') or 'Sem nome'}",
                f"Itens: {products_list}",
                f"Total: R${float(_field_val(order, 'total_amount', 0) or 0):.2f}",
                f"Endereço: {address_str}",
            ]
            if label_url:
                fallback_lines.append(f"Etiqueta: {label_url}")
            if tracking_code:
                fallback_lines.append(f"Rastreio: {tracking_code}")
            await send_whatsapp_message(to_phone=phone, text="\n".join(fallback_lines))


async def _send_pix_review_to_admins(
    order: Any,
    customer: Any,
    items: list[Any],
) -> None:
    """Envia template 'avaliar_pagamento_pix' (com botões confirmar/rejeitar) para admins."""
    products_list = ", ".join(
        f"{_field_val(i, 'product_name_snapshot', '')} ({_field_val(i, 'quantity', 1)}m)" for i in items
    )
    for phone in _admin_phones():
        _pending_pix_reviews[phone] = int(_field_val(order, "id", 0) or 0)
        result = await send_whatsapp_template(
            to_phone=phone,
            template_name=settings.pix_review_template_name,
            body_variables=[
                str(_field_val(order, "id", "")),
                _field_val(customer, "name") or _field_val(order, "customer_name") or "Sem nome",
                _field_val(customer, "whatsapp_phone") or _field_val(order, "customer_whatsapp") or "-",
                products_list,
                f"{float(_field_val(order, 'total_amount', 0) or 0):.2f}",
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
                customer_doc = await get_customer_firestore(customer_phone) if customer_phone else None
                await _generate_label_and_notify_admins(order, customer_doc or order, items)
                
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
                    customer_doc = await get_customer_firestore(customer_phone) if customer_phone else None
                    await _send_customer_tracking_update(order, customer_doc or order, items)
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
        await _generate_label_and_notify_admins(order, customer, items, session=session)
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


@app.post("/internal/orders/{order_id}/confirm-payment")
async def confirm_order_payment_internal(
    order_id: int,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Confirma manualmente um pagamento pendente e notifica o cliente.

    Usado por fluxos internos, como confirmação via agent com OTP administrativo.
    """
    from sqlmodel import select as sql_select
    from app.models import Order, OrderItem, OrderStatus, Customer

    try:
        body = await request.json()
    except Exception:
        body = {}

    approved_by = str(body.get("approved_by") or "admin")

    if settings.firestore_enabled:
        try:
            order = await get_order_firestore(order_id)
            if not order:
                raise HTTPException(status_code=404, detail=f"Pedido #{order_id} nao encontrado.")

            reviewable = ("payment_under_review", "awaiting_payment")
            if order.get("status") not in reviewable:
                return {
                    "confirmed": False,
                    "order_id": order_id,
                    "status": order.get("status"),
                    "message": "Pedido ja processado.",
                }

            await update_order_firestore(
                order_id,
                {
                    "status": "paid",
                    "payment_reference": f"manual:{approved_by}",
                    "updated_at": __import__("datetime").datetime.utcnow().isoformat(),
                },
            )

            items = await get_order_items_firestore(order_id)
            customer_phone = order.get("customer_whatsapp")
            if customer_phone:
                shipping_quote = order.get("shipping_quote_json") or {}
                delivery_days = shipping_quote.get("delivery_days_with_preparation")
                delivery_str = f"{delivery_days} dias uteis" if delivery_days else "a combinar"
                products_list = ", ".join(
                    f"{i.get('product_name_snapshot', '')} ({i.get('quantity', 1)}m)"
                    for i in items
                )
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

            customer_doc = await get_customer_firestore(customer_phone) if customer_phone else None
            await _generate_label_and_notify_admins(order, customer_doc or order, items)
            if customer_phone:
                await _send_customer_tracking_update(order, customer_doc or order, items)

            log.info("Pagamento confirmado manualmente via endpoint interno", extra={
                "event": "manual_payment_confirmed",
                "order_id": order_id,
                "approved_by": approved_by,
                "backend": "firestore",
            })
            return {"confirmed": True, "order_id": order_id, "status": "paid"}
        except HTTPException:
            raise
        except Exception as exc:
            log.error("Erro ao confirmar pagamento manual via Firestore: %s", exc)

    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Pedido #{order_id} nao encontrado.")

    reviewable = (OrderStatus.payment_under_review, OrderStatus.awaiting_payment)
    if order.status not in reviewable:
        return {
            "confirmed": False,
            "order_id": order_id,
            "status": order.status.value,
            "message": "Pedido ja processado.",
        }

    order.status = OrderStatus.paid
    order.payment_reference = f"manual:{approved_by}"
    session.add(order)
    session.commit()

    customer = session.get(Customer, order.customer_id)
    items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()
    if customer and customer.whatsapp_phone:
        shipping_quote = getattr(order, "shipping_quote_json", None) or {}
        delivery_days = shipping_quote.get("delivery_days_with_preparation")
        delivery_str = f"{delivery_days} dias uteis" if delivery_days else "a combinar"
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

    await _generate_label_and_notify_admins(order, customer, items, session=session)
    await _send_customer_tracking_update(order, customer, items)

    log.info("Pagamento confirmado manualmente via endpoint interno", extra={
        "event": "manual_payment_confirmed",
        "order_id": order_id,
        "approved_by": approved_by,
        "backend": "sql",
    })
    return {"confirmed": True, "order_id": order_id, "status": order.status.value}


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
    # Recovery opcional para clientes presos em contexto antigo de checkout.
    response = await run_agent_message(payload.user_id, payload.session_id, payload.message)
    if _looks_like_checkout_stuck(response):
        await reset_session(payload.user_id, payload.session_id)
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

    incoming_message = extract_incoming_message(raw)
    if customer and await _maybe_handle_pix_receipt(incoming_message, customer, session):
        return {"received": True, "pix_receipt_forwarded": True}

    message_text = extract_text_message(raw)

    if message_text and customer and customer.whatsapp_phone:
        lock = _user_locks.setdefault(customer.whatsapp_phone, asyncio.Lock())
        if lock.locked():
            # Mensagem duplicada chegou enquanto a anterior ainda processa — ignora.
            log.info(
                "Mensagem ignorada: processamento em andamento para o usuário",
                extra={"customer_phone": customer.whatsapp_phone},
            )
            return {"received": True}
        async with lock:
            try:
                _is_admin = customer.whatsapp_phone in _admin_phones()
                # Resposta principal sempre vem do ADK/LLM.
                agent_response = await _run_orchestrator_with_recovery(
                    customer_phone=customer.whatsapp_phone,
                    message_text=message_text,
                    is_admin=_is_admin,
                    session_id=customer.whatsapp_phone,
                )

                if agent_response and agent_response.strip():
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
                else:
                    # Em alguns fluxos (ex.: checkout Mercado Pago) o agente pode
                    # retornar vazio de forma intencional após enviar mensagem por tool.
                    # Nesses casos, não enviar fallback automático para evitar ruído.
                    log.warning(
                        "Resposta vazia do agente; nenhum fallback enviado",
                        extra={
                            "event": "empty_agent_response_fallback",
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
                # Notifica admins com contexto do cliente para acompanhamento manual.
                try:
                    customer_name = customer.name or "Nome não cadastrado"
                    admin_text = (
                        f"⚠️ *Erro no processamento — intervenção necessária*\n\n"
                        f"*Cliente:* {customer_name}\n"
                        f"*Telefone:* {customer.whatsapp_phone}\n"
                        f"*Última mensagem:* {message_text}\n\n"
                        f"O bot teve um erro ao processar essa mensagem. "
                        f"Por favor, entre em contato com o cliente para continuar o atendimento."
                    )
                    for admin_phone in _admin_phones():
                        await send_whatsapp_message(to_phone=admin_phone, text=admin_text)
                except Exception:
                    log.exception("Falha ao notificar admins sobre erro de processamento")

    return {"received": True}


@app.post("/internal/orders/{order_id}/notify-pix-pending")
async def notify_pix_pending(order_id: int, session: SessionDep) -> dict[str, Any]:
    """Envia notificações de PIX manual pendente ao cliente.

    A avaliação do admin só acontece depois que o cliente enviar o comprovante.
    """
    from sqlmodel import select as sql_select
    from app.models import Order, OrderItem, OrderStatus, Customer

    if settings.firestore_enabled:
        order = await get_order_firestore(order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Pedido #{order_id} não encontrado.")

        if order.get("status") == "draft":
            await update_order_status_firestore(order_id, "payment_under_review")
            order["status"] = "payment_under_review"

        customer_phone = order.get("customer_whatsapp") or order.get("customer_phone")
        customer = await get_customer_firestore(customer_phone) if customer_phone else None
        items = await get_order_items_firestore(order_id)
        products_list = ", ".join(
            f"{i.get('product_name_snapshot', '')} ({i.get('quantity', 1)}m)" for i in items
        )

        if customer_phone:
            client_result = await send_whatsapp_template(
                to_phone=customer_phone,
                template_name=settings.pix_awaiting_template_name,
                body_variables=[
                    ((customer or {}).get("name") or order.get("customer_name") or "Cliente").split()[0],
                    str(order_id),
                    products_list,
                    f"{float(order.get('total_amount', 0) or 0):.2f}",
                    settings.pix_key or "a combinar",
                ],
            )
            if isinstance(client_result, dict) and client_result.get("error"):
                log.error("Falha ao enviar pedido_aguardando_pix para %s: %s", customer_phone, client_result)

            await send_whatsapp_message(
                to_phone=customer_phone,
                text="Assim que fizer o PIX, envie o comprovante aqui em imagem ou PDF para conferência.",
            )

        log.info("Notificacao de PIX pendente enviada ao cliente para pedido #%s", order_id)
        return {"notified": True, "order_id": order_id}

    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Pedido #{order_id} não encontrado.")

    customer = session.get(Customer, order.customer_id)
    items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()

    if order.status == OrderStatus.draft:
        order.status = OrderStatus.payment_under_review
        session.add(order)
        session.commit()

    products_list = ", ".join(f"{i.product_name_snapshot} ({i.quantity}m)" for i in items)

    if customer and customer.whatsapp_phone:
        client_result = await send_whatsapp_template(
            to_phone=customer.whatsapp_phone,
            template_name=settings.pix_awaiting_template_name,
            body_variables=[
                (customer.name or "Cliente").split()[0],
                str(order.id),
                products_list,
                f"{order.total_amount:.2f}",
                settings.pix_key or "a combinar",
            ],
        )
        if isinstance(client_result, dict) and client_result.get("error"):
            log.error("Falha ao enviar pedido_aguardando_pix para %s: %s", customer.whatsapp_phone, client_result)

        await send_whatsapp_message(
            to_phone=customer.whatsapp_phone,
            text="Assim que fizer o PIX, envie o comprovante aqui em imagem ou PDF para conferência.",
        )

    log.info("Notificacao de PIX pendente enviada ao cliente para pedido #%s", order_id)
    return {"notified": True, "order_id": order_id}


@app.post("/internal/orders/{order_id}/regenerate-label")
async def regenerate_order_label(
    order_id: int,
    request: Request,
    session: SessionDep,
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Regera etiqueta do Melhor Envio para pedido pago.

    Requer token interno em `X-Scheduler-Token` (ou query param `token`).
    Por seguranca, nao recria quando ja existe `me_shipment_id`, a menos que `force=true`.
    """
    token = request.headers.get("X-Scheduler-Token") or request.query_params.get("token")
    _validate_scheduler_token(token)

    if not settings.melhor_envio_token:
        raise HTTPException(status_code=400, detail="MELHOR_ENVIO_TOKEN nao configurado.")

    # Firestore path
    if settings.firestore_enabled:
        order = await get_order_firestore(order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Pedido #{order_id} nao encontrado.")
        if order.get("status") != "paid":
            raise HTTPException(status_code=400, detail="Pedido ainda nao esta pago.")

        shipping_quote = order.get("shipping_quote_json") or {}
        if not shipping_quote.get("service_code"):
            raise HTTPException(status_code=400, detail="Pedido sem shipping_quote.service_code para gerar etiqueta.")

        existing_shipment = order.get("me_shipment_id")
        if existing_shipment and not force:
            return {
                "regenerated": False,
                "order_id": order_id,
                "message": (
                    "Pedido ja possui etiqueta/simulacao de etiqueta. "
                    "Use force=true apenas se quiser tentar nova geracao."
                ),
                "me_shipment_id": existing_shipment,
                "tracking_code": order.get("tracking_code"),
                "label_url": order.get("label_url"),
            }

        items = await get_order_items_firestore(order_id)

        class FakeOrder:
            pass

        fake_order = FakeOrder()
        for key, value in order.items():
            setattr(fake_order, key, value)

        customer_phone = order.get("customer_whatsapp") or order.get("customer_phone")
        customer_doc = await get_customer_firestore(customer_phone) if customer_phone else None

        try:
            label_result = await generate_label_for_order(fake_order, customer_doc or order, items)
        except Exception as exc:
            label_error_text = str(exc)
            if "from.document" in label_error_text:
                label_error_text = (
                    "CPF/CNPJ do remetente invalido no Melhor Envio "
                    "(configuracao ME_SENDER_DOCUMENT)."
                )
            raise HTTPException(status_code=502, detail=f"Falha ao gerar etiqueta: {label_error_text}") from exc

        await update_order_firestore(
            order_id,
            {
                "me_shipment_id": label_result.shipment_id,
                "tracking_code": label_result.tracking_code,
                "label_url": label_result.label_url,
            },
        )
        return {
            "regenerated": True,
            "order_id": order_id,
            "me_shipment_id": label_result.shipment_id,
            "tracking_code": label_result.tracking_code,
            "label_url": label_result.label_url,
        }

    # SQL path
    from sqlmodel import select as sql_select
    from app.models import Customer, Order, OrderItem, OrderStatus

    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Pedido #{order_id} nao encontrado.")
    if order.status != OrderStatus.paid:
        raise HTTPException(status_code=400, detail="Pedido ainda nao esta pago.")

    shipping_quote = getattr(order, "shipping_quote_json", None) or {}
    if not shipping_quote.get("service_code"):
        raise HTTPException(status_code=400, detail="Pedido sem shipping_quote.service_code para gerar etiqueta.")

    if getattr(order, "me_shipment_id", None) and not force:
        return {
            "regenerated": False,
            "order_id": order_id,
            "message": (
                "Pedido ja possui etiqueta/simulacao de etiqueta. "
                "Use force=true apenas se quiser tentar nova geracao."
            ),
            "me_shipment_id": order.me_shipment_id,
            "tracking_code": order.tracking_code,
            "label_url": order.label_url,
        }

    customer = session.get(Customer, order.customer_id)
    items = session.exec(sql_select(OrderItem).where(OrderItem.order_id == order.id)).all()

    if not customer:
        raise HTTPException(status_code=400, detail="Pedido sem cliente vinculado.")

    try:
        label_result = await generate_label_for_order(order, customer, items)
    except Exception as exc:
        label_error_text = str(exc)
        if "from.document" in label_error_text:
            label_error_text = (
                "CPF/CNPJ do remetente invalido no Melhor Envio "
                "(configuracao ME_SENDER_DOCUMENT)."
            )
        raise HTTPException(status_code=502, detail=f"Falha ao gerar etiqueta: {label_error_text}") from exc

    order.me_shipment_id = label_result.shipment_id
    order.tracking_code = label_result.tracking_code
    order.label_url = label_result.label_url
    session.add(order)
    session.commit()

    return {
        "regenerated": True,
        "order_id": order_id,
        "me_shipment_id": label_result.shipment_id,
        "tracking_code": label_result.tracking_code,
        "label_url": label_result.label_url,
    }


@app.delete("/internal/delete-customer/{phone}")
async def delete_customer_endpoint(phone: str, request: Request) -> dict[str, Any]:
    """Apaga TODOS os dados do cliente no Firestore (pedidos, carrinhos, cadastro) e reseta a sessão ADK.

    IRREVERSÍVEL. Requer header X-Scheduler-Token.
    """
    token = request.headers.get("X-Scheduler-Token") or request.query_params.get("token")
    _validate_scheduler_token(token)

    if not settings.firestore_enabled:
        raise HTTPException(status_code=400, detail="Firestore desabilitado.")

    result = await delete_customer_data_firestore(phone)
    await reset_session(user_id=phone, session_id=phone)
    log.warning("Dados do cliente %s apagados: %s", phone, result)
    return {"phone": phone, "deleted": result}


@app.post("/internal/test-templates/{phone}")
async def test_templates_endpoint(phone: str, request: Request) -> dict[str, Any]:
    """Envia os 4 templates WhatsApp com dados fictícios para o número informado.

    Útil para validar templates aprovados pela Meta. Requer X-Scheduler-Token.
    """
    token = request.headers.get("X-Scheduler-Token") or request.query_params.get("token")
    _validate_scheduler_token(token)

    templates = [
        {
            "name": settings.pix_awaiting_template_name,
            "variables": [
                "Santiago",
                "999",
                "Helanca Verde (2m), Malha Branca (1m)",
                "150,00",
                settings.pix_key or "11.999.999/0001-00",
            ],
        },
        {
            "name": settings.order_confirmed_template_name,
            "variables": [
                "Santiago",
                "999",
                "Helanca Verde (2m), Malha Branca (1m)",
                "150,00",
                "5 dias uteis",
            ],
        },
        {
            "name": settings.pix_review_template_name,
            "variables": [
                "999",
                "Santiago Teste",
                phone,
                "Helanca Verde (2m), Malha Branca (1m)",
                "150,00",
            ],
        },
        {
            "name": settings.order_separation_template_name,
            "variables": [
                "999",
                "Santiago Teste",
                "Helanca Verde (2m), Malha Branca (1m)",
                "150,00",
                "01310-100, n 45",
                "https://etiqueta.exemplo.com/999.pdf",
                "BR123456789BR",
            ],
        },
    ]

    results = {}
    for tpl in templates:
        result = await send_whatsapp_template(
            to_phone=phone,
            template_name=tpl["name"],
            body_variables=tpl["variables"],
        )
        if isinstance(result, dict) and result.get("error"):
            results[tpl["name"]] = {"status": "error", "detail": result}
        else:
            msg_id = (result.get("messages") or [{}])[0].get("id", "?")
            results[tpl["name"]] = {"status": "ok", "message_id": msg_id}

    log.info("test-templates enviados para %s: %s", phone, results)
    return {"phone": phone, "results": results}


@app.post("/internal/reset-session/{phone}")
async def reset_agent_session(phone: str, request: Request) -> dict[str, Any]:
    """Apaga e recria a sessão ADK de um número, zerando o histórico da conversa."""
    token = request.headers.get("X-Scheduler-Token") or request.query_params.get("token")
    if token != settings.scheduler_token:
        raise HTTPException(status_code=401, detail="Token inválido.")
    await reset_session(user_id=phone, session_id=phone)
    log.info("Sessão resetada para %s", phone)
    return {"reset": True, "phone": phone}


@app.post("/internal/maintenance/cleanup-expired-orders")
def cleanup_expired_orders(
    request: Request,
    dry_run: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Remove pedidos em aberto expirados (mais de 48h) no Firestore.

    Statuses considerados "em aberto": awaiting_shipping_choice,
    awaiting_payment_method e awaiting_payment.
    
    Requer autenticação via header X-Scheduler-Token.
    """
    # Validar token de autenticação
    token = request.headers.get("X-Scheduler-Token")
    _validate_scheduler_token(token)
    
    if not settings.firestore_enabled:
        raise HTTPException(status_code=400, detail="Firestore desabilitado.")

    expired_orders = list_expired_open_orders_firestore(limit=limit)
    if dry_run:
        return {
            "dry_run": True,
            "found": len(expired_orders),
            "deleted": 0,
            "orders": [
                {
                    "order_id": o.get("id"),
                    "order_number": o.get("order_number"),
                    "status": o.get("status"),
                    "expires_at": o.get("expires_at"),
                    "last_modified_at": o.get("last_modified_at"),
                }
                for o in expired_orders
            ],
        }

    deleted = 0
    for order in expired_orders:
        order_id = order.get("id")
        if not order_id:
            continue
        if delete_order_firestore(int(order_id)):
            deleted += 1

    log.info(
        "Cleanup de pedidos expirados executado",
        extra={
            "event": "cleanup_expired_orders",
            "found": len(expired_orders),
            "deleted": deleted,
            "limit": limit,
        },
    )

    return {
        "dry_run": False,
        "found": len(expired_orders),
        "deleted": deleted,
    }


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

                shipping_quote = order.get("shipping_quote_json") or {}
                customer_doc = await get_customer_firestore(customer_phone) if customer_phone else None
                await _generate_label_and_notify_admins(order, customer_doc or order, items)

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
                        await send_whatsapp_message(
                            to_phone=customer_phone,
                            text=(
                                f"Pagamento aprovado com sucesso para o pedido #{order_id}. "
                                "Agora vamos iniciar a separacao para envio."
                            ),
                        )
                    else:
                        log.info("Template pedido_confirmado enviado para %s", customer_phone)

                    await _send_customer_tracking_update(order, customer_doc or order, items)

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

        await _generate_label_and_notify_admins(order, customer, items, session=session)

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
                await send_whatsapp_message(
                    to_phone=customer.whatsapp_phone,
                    text=(
                        f"Pagamento aprovado com sucesso para o pedido #{order.id}. "
                        "Agora vamos iniciar a separacao para envio."
                    ),
                )
            else:
                log.info("Template pedido_confirmado enviado para %s", customer.whatsapp_phone)

            await _send_customer_tracking_update(order, customer, items)

    except Exception as exc:
        log.error("Erro ao processar webhook Mercado Pago: %s", exc)

    return {"received": True}