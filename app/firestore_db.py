"""Firestore database service para operações de pedidos e clientes."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.models import OrderStatus

log = logging.getLogger(__name__)

_db_instance: Any | None = None


def init_firestore() -> Any:
    """Inicializa Firebase com credenciais do GCP."""
    global _db_instance
    if _db_instance is not None:
        return _db_instance

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore, initialize_app

        settings = get_settings()

        try:
            # Em Cloud Run, usa credenciais padrão do GCP
            if not credentials.get_credential_from_environ():
                initialize_app()
            else:
                initialize_app()
        except ValueError:
            # App já inicializado
            pass

        _db_instance = firestore.client()
        log.info("Firestore inicializado com sucesso")
        return _db_instance
    except ImportError as e:
        log.error("firebase-admin nao instalado: %s", e)
        raise


def get_firestore() -> Any:
    """Retorna instância do Firestore."""
    global _db_instance
    if _db_instance is None:
        return init_firestore()
    return _db_instance


# --- CUSTOMER ---


async def upsert_customer_firestore(
    whatsapp_phone: str,
    name: str | None = None,
    email: str | None = None,
    zipcode: str | None = None,
    address_number: str | None = None,
) -> dict[str, Any]:
    """Insere ou atualiza cliente no Firestore."""
    db = get_firestore()
    customer_ref = db.collection("customers").document(whatsapp_phone)

    data = await customer_ref.get()
    if data.exists:
        # Atualiza campos não-nulos
        updates = {}
        if name:
            updates["name"] = name
        if email:
            updates["email"] = email
        if zipcode:
            updates["zipcode"] = zipcode
        if address_number:
            updates["address_number"] = address_number
        if updates:
            await customer_ref.update(updates)
        result = data.to_dict()
        result.update(updates)
    else:
        # Cria novo
        result = {
            "whatsapp_phone": whatsapp_phone,
            "name": name,
            "email": email,
            "zipcode": zipcode,
            "address_number": address_number,
            "is_admin": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        await customer_ref.set(result)

    return result


async def get_customer_firestore(whatsapp_phone: str) -> dict[str, Any] | None:
    """Busca cliente pelo WhatsApp."""
    db = get_firestore()
    doc = await db.collection("customers").document(whatsapp_phone).get()
    return doc.to_dict() if doc.exists else None


# --- ORDER ---


async def create_order_firestore(
    customer_whatsapp: str,
    shipping_zipcode: str,
    subtotal: float,
    shipping_amount: float,
    total_amount: float,
    shipping_quote_json: dict | None = None,
) -> dict[str, Any]:
    """Cria novo pedido no Firestore."""
    db = get_firestore()
    now = datetime.utcnow().isoformat()

    order_data = {
        "customer_whatsapp": customer_whatsapp,
        "channel": "whatsapp",
        "shipping_zipcode": shipping_zipcode,
        "subtotal_amount": subtotal,
        "shipping_amount": shipping_amount,
        "total_amount": total_amount,
        "shipping_quote_json": shipping_quote_json or {},
        "status": OrderStatus.draft.value,
        "payment_provider": None,
        "payment_reference": None,
        "me_shipment_id": None,
        "tracking_code": None,
        "label_url": None,
        "created_at": now,
        "updated_at": now,
    }

    doc_ref = await db.collection("orders").add(order_data)
    order_id = int(doc_ref.id) if doc_ref.id.isdigit() else hash(doc_ref.id) % 1000000
    await doc_ref.update({"id": order_id})

    order_data["id"] = order_id
    order_data["_doc_id"] = doc_ref.id
    return order_data


async def get_order_firestore(order_id: int) -> dict[str, Any] | None:
    """Busca pedido pelo ID."""
    db = get_firestore()
    docs = await db.collection("orders").where("id", "==", order_id).limit(1).stream()
    async for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id
        return data
    return None


async def update_order_firestore(order_id: int, updates: dict[str, Any]) -> bool:
    """Atualiza pedido."""
    db = get_firestore()
    docs = await db.collection("orders").where("id", "==", order_id).limit(1).stream()
    async for doc in docs:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.collection("orders").document(doc.id).update(updates)
        return True
    return False


async def update_order_status_firestore(order_id: int, status: OrderStatus) -> bool:
    """Atualiza status do pedido."""
    return await update_order_firestore(order_id, {"status": status.value})


# --- ORDER ITEMS ---


async def add_order_item_firestore(
    order_id: int,
    product_id: int,
    product_name: str,
    unit_price: float,
    quantity: float,
) -> dict[str, Any]:
    """Adiciona item ao pedido."""
    db = get_firestore()
    line_total = round(unit_price * quantity, 2)

    item_data = {
        "order_id": order_id,
        "product_id": product_id,
        "product_name_snapshot": product_name,
        "unit_price_snapshot": unit_price,
        "quantity": quantity,
        "line_total": line_total,
    }

    await db.collection("order_items").add(item_data)
    return item_data


async def get_order_items_firestore(order_id: int) -> list[dict[str, Any]]:
    """Retorna itens do pedido."""
    db = get_firestore()
    docs = await db.collection("order_items").where("order_id", "==", order_id).stream()
    items = []
    async for doc in docs:
        items.append(doc.to_dict())
    return items


# --- CART ---


async def get_open_cart_firestore(customer_whatsapp: str) -> dict[str, Any] | None:
    """Busca carrinho aberto do cliente."""
    db = get_firestore()
    docs = await (
        db.collection("carts")
        .where("customer_whatsapp", "==", customer_whatsapp)
        .where("status", "==", "open")
        .limit(1)
        .stream()
    )
    async for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id
        return data
    return None


async def create_cart_firestore(customer_whatsapp: str) -> dict[str, Any]:
    """Cria novo carrinho."""
    db = get_firestore()
    now = datetime.utcnow().isoformat()

    cart_data = {
        "customer_whatsapp": customer_whatsapp,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }

    doc_ref = await db.collection("carts").add(cart_data)
    cart_data["_doc_id"] = doc_ref.id
    return cart_data


async def add_to_cart_firestore(
    cart_id: str,
    product_id: int,
    product_name: str,
    unit_price: float,
    quantity: float,
) -> dict[str, Any]:
    """Adiciona produto ao carrinho."""
    db = get_firestore()
    line_total = round(unit_price * quantity, 2)

    item_data = {
        "cart_id": cart_id,
        "product_id": product_id,
        "product_name": product_name,
        "unit_price": unit_price,
        "quantity": quantity,
        "line_total": line_total,
    }

    await db.collection("cart_items").add(item_data)
    return item_data


async def get_cart_items_firestore(cart_id: str) -> list[dict[str, Any]]:
    """Retorna itens do carrinho."""
    db = get_firestore()
    docs = await db.collection("cart_items").where("cart_id", "==", cart_id).stream()
    items = []
    async for doc in docs:
        items.append(doc.to_dict())
    return items


async def clear_cart_firestore(cart_id: str) -> None:
    """Limpa carrinho."""
    db = get_firestore()
    docs = await db.collection("cart_items").where("cart_id", "==", cart_id).stream()
    async for doc in docs:
        await db.collection("cart_items").document(doc.id).delete()
