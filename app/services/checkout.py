from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Customer, Order, OrderItem, OrderStatus, Product
from app.repositories import get_customer_by_phone, upsert_customer
from app.firestore_db import create_order_firestore, add_order_item_firestore
from app.schemas import (
    CheckoutItemRequest,
    CheckoutItemResponse,
    CheckoutQuoteRequest,
    CheckoutQuoteResponse,
    CustomerUpsert,
    ShippingProvider,
    ShippingQuoteRequest,
)
from app.services.shipping import calculate_shipping_quote
from app.utils.phones import normalize_phone


def _sanitize_zipcode(zipcode: str) -> str:
    return "".join(char for char in zipcode if char.isdigit())


def _resolve_customer(session: Session, payload: CheckoutQuoteRequest) -> Customer:
    customer = upsert_customer(
        session,
        CustomerUpsert(
            whatsapp_phone=payload.whatsapp_phone,
            name=payload.customer_name,
            email=payload.customer_email,
            zipcode=_sanitize_zipcode(payload.zipcode) if payload.zipcode else None,
        ),
    )
    session.refresh(customer)
    return customer


def _get_effective_zipcode(customer: Customer, payload: CheckoutQuoteRequest) -> str | None:
    if payload.zipcode:
        return _sanitize_zipcode(payload.zipcode)
    if customer.zipcode:
        return _sanitize_zipcode(customer.zipcode)
    return None


def _aggregate_dimensions(items: list[tuple[Product, float]]) -> tuple[float, float, float, float]:
    total_weight_g = 0.0
    max_length_cm = 0.0
    max_width_cm = 0.0
    total_height_cm = 0.0

    for product, quantity in items:
        if product.weight_g is None or product.package_length_cm is None or product.package_width_cm is None or product.package_height_cm is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Produto sem peso ou dimensoes de embalagem cadastrados: {product.name}",
            )
        total_weight_g += product.weight_g * quantity
        max_length_cm = max(max_length_cm, product.package_length_cm)
        max_width_cm = max(max_width_cm, product.package_width_cm)
        total_height_cm += product.package_height_cm * quantity

    return total_weight_g, max_length_cm, max_width_cm, min(total_height_cm, 100.0)


def _load_cart_items(session: Session, items: list[CheckoutItemRequest]) -> tuple[list[tuple[Product, float]], list[CheckoutItemResponse], float]:
    cart_items: list[tuple[Product, float]] = []
    response_items: list[CheckoutItemResponse] = []
    subtotal_amount = 0.0

    for item in items:
        product = session.get(Product, item.product_id)
        if product is None or not product.active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Produto nao encontrado: {item.product_id}")

        line_total = round(product.price * item.quantity, 2)
        subtotal_amount += line_total
        cart_items.append((product, item.quantity))
        response_items.append(
            CheckoutItemResponse(
                product_id=product.id or item.product_id,
                name=product.name,
                quantity=item.quantity,
                unit_price=product.price,
                line_total=line_total,
            )
        )

    return cart_items, response_items, round(subtotal_amount, 2)


async def create_checkout_quote(session: Session, payload: CheckoutQuoteRequest) -> CheckoutQuoteResponse:
    settings = get_settings()
    normalized_phone = normalize_phone(payload.whatsapp_phone)
    customer = _resolve_customer(session, payload)
    effective_zipcode = _get_effective_zipcode(customer, payload)

    if not effective_zipcode:
        return CheckoutQuoteResponse(
            requires_zipcode=True,
            message="Para calcular o pedido, preciso primeiro do CEP de entrega.",
            customer_whatsapp_phone=normalized_phone,
        )

    if customer.zipcode != effective_zipcode:
        customer.zipcode = effective_zipcode
        customer.updated_at = datetime.utcnow()
        session.add(customer)
        session.commit()
        session.refresh(customer)

    cart_items, response_items, subtotal_amount = _load_cart_items(session, payload.items)
    total_weight_g, package_length_cm, package_width_cm, package_height_cm = _aggregate_dimensions(cart_items)

    shipping_request = ShippingQuoteRequest(
        provider=ShippingProvider.melhor_envio,
        to_zipcode=effective_zipcode,
        product_name="Carrinho Diotex Tecidos",
        quantity=1,
        unit_price=subtotal_amount,
        weight_g=total_weight_g,
        package_length_cm=package_length_cm,
        package_width_cm=package_width_cm,
        package_height_cm=package_height_cm,
        insurance_value=subtotal_amount,
    )
    shipping_response = await calculate_shipping_quote(shipping_request)
    if not shipping_response.options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nao foi possivel calcular frete para este carrinho.")

    selected_option = shipping_response.options[0]
    shipping_amount = round(selected_option.price, 2)
    total_amount = round(subtotal_amount + shipping_amount, 2)

    # Tenta usar Firestore se habilitado
    if settings.firestore_enabled:
        try:
            order_id = await create_order_firestore(
                customer_whatsapp=customer.whatsapp_phone,
                customer_name=customer.name or "",
                customer_email=customer.email or "",
                customer_cpf=customer.cpf or "",
                channel=payload.channel,
                shipping_zipcode=effective_zipcode,
                shipping_quote_json=selected_option.model_dump(),
                subtotal_amount=subtotal_amount,
                shipping_amount=shipping_amount,
                total_amount=total_amount,
                status="draft",
            )

            # Adiciona items do pedido
            for item in response_items:
                await add_order_item_firestore(
                    order_id=order_id,
                    product_id=item.product_id,
                    product_name_snapshot=item.name,
                    unit_price_snapshot=item.unit_price,
                    quantity=item.quantity,
                    line_total=item.line_total,
                )

            return CheckoutQuoteResponse(
                requires_zipcode=False,
                customer_whatsapp_phone=normalized_phone,
                zipcode_used=effective_zipcode,
                order_id=order_id,
                provider=selected_option.provider,
                carrier_name=selected_option.carrier_name,
                service_name=selected_option.service_name,
                shipping_amount=shipping_amount,
                subtotal_amount=subtotal_amount,
                total_amount=total_amount,
                delivery_days_with_preparation=selected_option.delivery_days_with_preparation,
                items=response_items,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Falha ao criar order via Firestore, fallback para SQLAlchemy: %s", exc)
            # Continua com SQLAlchemy

    # Fallback para SQLAlchemy
    order = Order(
        customer_id=customer.id or 0,
        channel=payload.channel,
        shipping_zipcode=effective_zipcode,
        shipping_quote_json=selected_option.model_dump(),
        subtotal_amount=subtotal_amount,
        shipping_amount=shipping_amount,
        total_amount=total_amount,
        status=OrderStatus.draft,
    )
    session.add(order)
    session.flush()

    for item in response_items:
        session.add(
            OrderItem(
                order_id=order.id or 0,
                product_id=item.product_id,
                product_name_snapshot=item.name,
                unit_price_snapshot=item.unit_price,
                quantity=item.quantity,
                line_total=item.line_total,
            )
        )

    session.commit()
    session.refresh(order)

    return CheckoutQuoteResponse(
        requires_zipcode=False,
        customer_whatsapp_phone=normalized_phone,
        zipcode_used=effective_zipcode,
        order_id=order.id,
        provider=selected_option.provider,
        carrier_name=selected_option.carrier_name,
        service_name=selected_option.service_name,
        shipping_amount=shipping_amount,
        subtotal_amount=subtotal_amount,
        total_amount=total_amount,
        delivery_days_with_preparation=selected_option.delivery_days_with_preparation,
        items=response_items,
    )