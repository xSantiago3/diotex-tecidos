from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import Customer, Order, OrderItem, OtpChallenge
from app.schemas import CustomerUpsert, OrderItemSummary, OrderStatusResponse
from app.utils.phones import normalize_phone


def upsert_customer(session: Session, data: CustomerUpsert) -> Customer:
    normalized_phone = normalize_phone(data.whatsapp_phone)
    customer = session.exec(select(Customer).where(Customer.whatsapp_phone == normalized_phone)).first()
    if customer is None:
        customer = Customer(
            whatsapp_phone=normalized_phone,
            name=data.name,
            email=data.email,
            zipcode=data.zipcode,
            preferred_language=data.preferred_language or "pt-BR",
        )
        session.add(customer)
    else:
        if data.name is not None:
            customer.name = data.name
        if data.email is not None:
            customer.email = data.email
        if data.zipcode is not None:
            customer.zipcode = data.zipcode
        if data.preferred_language is not None:
            customer.preferred_language = data.preferred_language
        customer.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(customer)
    return customer


def get_customer_by_phone(session: Session, whatsapp_phone: str) -> Customer | None:
    normalized_phone = normalize_phone(whatsapp_phone)
    return session.exec(select(Customer).where(Customer.whatsapp_phone == normalized_phone)).first()


def get_order_for_customer(session: Session, whatsapp_phone: str, order_id: int) -> OrderStatusResponse:
    normalized_phone = normalize_phone(whatsapp_phone)
    customer = get_customer_by_phone(session, normalized_phone)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente nao encontrado.")

    order = session.exec(
        select(Order).where(Order.id == order_id, Order.customer_id == customer.id)
    ).first()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido nao encontrado para este numero.")

    items = session.exec(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    item_summaries = [
        OrderItemSummary(
            product_name=item.product_name_snapshot,
            quantity=item.quantity,
            unit_price=item.unit_price_snapshot,
            line_total=item.line_total,
        )
        for item in items
    ]
    return OrderStatusResponse(
        order_id=order.id,
        customer_whatsapp_phone=normalized_phone,
        status=order.status,
        subtotal_amount=order.subtotal_amount,
        shipping_amount=order.shipping_amount,
        total_amount=order.total_amount,
        channel=order.channel,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=item_summaries,
        shipping_quote=order.shipping_quote_json,
    )


def create_otp_challenge(session: Session, challenge: OtpChallenge) -> OtpChallenge:
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return challenge


def get_active_otp_challenge(session: Session, whatsapp_phone: str, purpose: str) -> OtpChallenge | None:
    now = datetime.utcnow()
    normalized_phone = normalize_phone(whatsapp_phone)
    return session.exec(
        select(OtpChallenge)
        .where(
            OtpChallenge.actor_phone == normalized_phone,
            OtpChallenge.purpose == purpose,
            OtpChallenge.used_at.is_(None),
            OtpChallenge.expires_at >= now,
        )
        .order_by(OtpChallenge.created_at.desc())
    ).first()