"""Firestore database service para operações de pedidos e clientes."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.config import get_settings
from app.models import OrderStatus

log = logging.getLogger(__name__)

_db_instance: Any | None = None
OPEN_ORDER_STATUSES = {
    "awaiting_shipping_choice",
    "awaiting_payment_method",
    "awaiting_payment",
}
ORDER_EXPIRATION_HOURS = 48


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return _now_iso()


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def _build_order_number(order_id: int) -> str:
    dt = datetime.utcnow()
    return f"DTX-{dt:%Y%m%d}-{order_id:06d}"


def _next_order_id_firestore(db: Any) -> int:
    """Gera ID sequencial para pedidos no Firestore."""
    from firebase_admin import firestore

    counter_ref = db.collection("metadata").document("order_counter")
    transaction = db.transaction()

    @firestore.transactional
    def _reserve_id(txn: Any) -> int:
        snapshot = counter_ref.get(transaction=txn)
        last_id = 0
        if snapshot.exists:
            raw = snapshot.to_dict().get("last_id")
            try:
                last_id = int(raw or 0)
            except Exception:
                last_id = 0
        next_id = last_id + 1
        txn.set(
            counter_ref,
            {"last_id": next_id, "updated_at": _now_iso()},
            merge=True,
        )
        return next_id

    return int(_reserve_id(transaction))


def _compute_expires_at_for_status(status: str, base_time: datetime | None = None) -> str | None:
    if status not in OPEN_ORDER_STATUSES:
        return None
    base = base_time or datetime.utcnow()
    return (base + timedelta(hours=ORDER_EXPIRATION_HOURS)).isoformat()


def init_firestore() -> Any:
    """Inicializa Firebase com credenciais do GCP."""
    global _db_instance
    if _db_instance is not None:
        return _db_instance

    try:
        import firebase_admin
        from firebase_admin import firestore, initialize_app

        try:
            # Reutiliza app já inicializado quando existir.
            firebase_admin.get_app()
        except ValueError:
            # Em Cloud Run, initialize_app() usa Application Default Credentials.
            initialize_app()

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
    cpf: str | None = None,
    zipcode: str | None = None,
    address_number: str | None = None,
) -> dict[str, Any]:
    """Insere ou atualiza cliente no Firestore."""
    db = get_firestore()
    customer_ref = db.collection("customers").document(whatsapp_phone)

    data = customer_ref.get()
    if data.exists:
        # Atualiza campos não-nulos
        updates = {}
        if name:
            updates["name"] = name
        if email:
            updates["email"] = email
        if cpf:
            updates["cpf"] = cpf
        if zipcode:
            updates["zipcode"] = zipcode
        if address_number:
            updates["address_number"] = address_number
        if updates:
            customer_ref.update(updates)
        result = data.to_dict()
        result.update(updates)
    else:
        # Cria novo
        result = {
            "whatsapp_phone": whatsapp_phone,
            "name": name,
            "email": email,
            "cpf": cpf,
            "zipcode": zipcode,
            "address_number": address_number,
            "is_admin": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        customer_ref.set(result)

    return result


async def get_customer_firestore(whatsapp_phone: str) -> dict[str, Any] | None:
    """Busca cliente pelo WhatsApp."""
    db = get_firestore()
    doc = db.collection("customers").document(whatsapp_phone).get()
    return doc.to_dict() if doc.exists else None


# --- ORDER ---


async def create_order_firestore(
    customer_whatsapp: str,
    shipping_zipcode: str = "",
    subtotal: float | None = None,
    shipping_amount: float = 0.0,
    total_amount: float | None = None,
    shipping_quote_json: dict | None = None,
    customer_name: str = "",
    customer_email: str = "",
    customer_cpf: str = "",
    channel: str = "whatsapp",
    subtotal_amount: float | None = None,
    status: str = "awaiting_payment",
    payment_provider: str | None = None,
    payment_reference: str | None = None,
    tracking_code: str | None = None,
    carrier_name: str | None = None,
    address_number: str | None = None,
) -> int:
    """Cria novo pedido no Firestore."""
    db = get_firestore()
    now = _now_iso()
    effective_subtotal = float(subtotal_amount if subtotal_amount is not None else (subtotal if subtotal is not None else 0.0))
    effective_total = float(total_amount if total_amount is not None else (effective_subtotal + float(shipping_amount)))
    effective_carrier = carrier_name or (shipping_quote_json or {}).get("carrier_name")
    expires_at = _compute_expires_at_for_status(status, datetime.utcnow())

    order_id = _next_order_id_firestore(db)
    order_data = {
        "id": order_id,
        "order_number": _build_order_number(order_id),
        "customer_id": customer_whatsapp,
        "customer_phone": customer_whatsapp,
        "customer_whatsapp": customer_whatsapp,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_cpf": customer_cpf,
        "channel": channel,
        "shipping_zipcode": shipping_zipcode,
        "address_number": address_number,
        "subtotal_amount": effective_subtotal,
        "shipping_amount": float(shipping_amount),
        "total_amount": effective_total,
        "shipping_quote_json": shipping_quote_json or {},
        "status": status,
        "payment_provider": payment_provider,
        "payment_reference": payment_reference,
        "carrier_name": effective_carrier,
        "me_shipment_id": None,
        "tracking_code": tracking_code,
        "label_url": None,
        "items": [],
        "created_at": now,
        "updated_at": now,
        "last_modified_at": now,
        "expires_at": expires_at,
    }

    db.collection("orders").document(str(order_id)).set(order_data)

    return order_id


async def get_order_firestore(order_id: int) -> dict[str, Any] | None:
    """Busca pedido pelo ID."""
    db = get_firestore()
    docs = db.collection("orders").where("id", "==", order_id).limit(1).stream()
    for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id
        return data
    return None


async def get_latest_pending_pix_order_firestore(customer_whatsapp: str) -> dict[str, Any] | None:
    """Retorna o pedido PIX manual mais recente ainda aguardando comprovacao do cliente/admin."""
    db = get_firestore()
    docs = (
        db.collection("orders")
        .where("customer_whatsapp", "==", customer_whatsapp)
        .where("payment_provider", "==", "pix_manual")
        .where("status", "==", "payment_under_review")
        .stream()
    )
    latest_order: dict[str, Any] | None = None
    latest_timestamp: datetime | None = None
    for doc in docs:
        data = doc.to_dict() or {}
        updated_at = _to_datetime(data.get("updated_at")) or _to_datetime(data.get("created_at"))
        if latest_order is None or (updated_at and (latest_timestamp is None or updated_at > latest_timestamp)):
            data["_doc_id"] = doc.id
            latest_order = data
            latest_timestamp = updated_at
    return latest_order


async def update_order_firestore(order_id: int, updates: dict[str, Any]) -> bool:
    """Atualiza pedido."""
    db = get_firestore()
    docs = db.collection("orders").where("id", "==", order_id).limit(1).stream()
    for doc in docs:
        current = doc.to_dict() or {}
        status = str(updates.get("status", current.get("status", "")))
        now = _now_iso()
        updates["updated_at"] = now
        updates["last_modified_at"] = now
        # Pedido aberto expira em 48h; pedidos finais nao possuem expiracao ativa.
        updates["expires_at"] = _compute_expires_at_for_status(status, datetime.utcnow())
        db.collection("orders").document(doc.id).update(updates)
        return True
    return False


async def update_order_status_firestore(order_id: int, status: OrderStatus | str) -> bool:
    """Atualiza status do pedido."""
    normalized = status.value if isinstance(status, OrderStatus) else str(status)
    return await update_order_firestore(order_id, {"status": normalized})


# --- ORDER ITEMS ---


async def add_order_item_firestore(
    order_id: int,
    product_id: int,
    quantity: float,
    product_name: str | None = None,
    unit_price: float | None = None,
    line_total: float | None = None,
    product_name_snapshot: str | None = None,
    unit_price_snapshot: float | None = None,
) -> dict[str, Any]:
    """Adiciona item ao pedido."""
    db = get_firestore()
    resolved_name = product_name_snapshot or product_name or "Produto"
    resolved_unit_price = float(unit_price_snapshot if unit_price_snapshot is not None else (unit_price if unit_price is not None else 0.0))
    resolved_line_total = round(float(line_total) if line_total is not None else (resolved_unit_price * quantity), 2)

    item_data = {
        "order_id": order_id,
        "product_id": product_id,
        "product_name_snapshot": resolved_name,
        "unit_price_snapshot": resolved_unit_price,
        "quantity": quantity,
        "line_total": resolved_line_total,
    }

    db.collection("order_items").add(item_data)

    # Mantem snapshot simplificado no documento principal para leituras rapidas.
    order_docs = db.collection("orders").where("id", "==", order_id).limit(1).stream()
    for order_doc in order_docs:
        order_ref = db.collection("orders").document(order_doc.id)
        order_data = order_doc.to_dict() or {}
        current_items = order_data.get("items") or []
        current_items.append(item_data)
        now = _now_iso()
        order_ref.update({"items": current_items, "updated_at": now, "last_modified_at": now})
        break

    return item_data


async def get_order_items_firestore(order_id: int) -> list[dict[str, Any]]:
    """Retorna itens do pedido."""
    db = get_firestore()
    docs = db.collection("order_items").where("order_id", "==", order_id).stream()
    items = []
    for doc in docs:
        items.append(doc.to_dict())
    return items


# --- CART ---


async def get_open_cart_firestore(customer_whatsapp: str) -> dict[str, Any] | None:
    """Busca carrinho aberto do cliente."""
    db = get_firestore()
    docs = (
        db.collection("carts")
        .where("customer_whatsapp", "==", customer_whatsapp)
        .where("status", "==", "open")
        .limit(1)
        .stream()
    )
    for doc in docs:
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

    _, doc_ref = db.collection("carts").add(cart_data)
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

    db.collection("cart_items").add(item_data)
    return item_data


async def get_cart_items_firestore(cart_id: str) -> list[dict[str, Any]]:
    """Retorna itens do carrinho."""
    db = get_firestore()
    docs = db.collection("cart_items").where("cart_id", "==", cart_id).stream()
    items = []
    for doc in docs:
        items.append(doc.to_dict())
    return items


async def clear_cart_firestore(cart_id: str) -> None:
    """Limpa carrinho."""
    db = get_firestore()
    docs = db.collection("cart_items").where("cart_id", "==", cart_id).stream()
    for doc in docs:
        db.collection("cart_items").document(doc.id).delete()


def list_expired_open_orders_firestore(limit: int = 200) -> list[dict[str, Any]]:
    """Lista pedidos em aberto com expiracao vencida para limpeza."""
    db = get_firestore()
    now = datetime.utcnow()
    docs = db.collection("orders").where("status", "in", list(OPEN_ORDER_STATUSES)).limit(limit).stream()
    expired: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        expires_at = _to_datetime(data.get("expires_at"))
        # Compatibilidade com pedidos antigos sem expires_at.
        if not expires_at:
            last_modified = _to_datetime(data.get("last_modified_at")) or _to_datetime(data.get("updated_at")) or _to_datetime(data.get("created_at"))
            if not last_modified:
                continue
            expires_at = last_modified + timedelta(hours=ORDER_EXPIRATION_HOURS)
        if expires_at <= now:
            data["_doc_id"] = doc.id
            expired.append(data)
    return expired


async def delete_customer_data_firestore(whatsapp_phone: str) -> dict[str, int]:
    """Apaga TODOS os dados de um cliente do Firestore: pedidos, itens, carrinhos, itens de carrinho e cadastro.

    Retorna contagens do que foi removido.
    """
    db = get_firestore()

    # 1. Pedidos do cliente + seus itens
    order_ids: list[int] = []
    order_doc_ids: list[str] = []
    for field in ("customer_whatsapp", "customer_phone"):
        docs = db.collection("orders").where(field, "==", whatsapp_phone).stream()
        for doc in docs:
            data = doc.to_dict() or {}
            oid = data.get("id")
            if oid is not None and oid not in order_ids:
                order_ids.append(int(oid))
                order_doc_ids.append(doc.id)

    deleted_order_items = 0
    for oid in order_ids:
        item_docs = db.collection("order_items").where("order_id", "==", oid).stream()
        for item_doc in item_docs:
            db.collection("order_items").document(item_doc.id).delete()
            deleted_order_items += 1

    for doc_id in order_doc_ids:
        db.collection("orders").document(doc_id).delete()

    # 2. Carrinhos + itens de carrinho
    cart_ids: list[str] = []
    cart_docs = db.collection("carts").where("customer_whatsapp", "==", whatsapp_phone).stream()
    for doc in cart_docs:
        cart_ids.append(doc.id)
        db.collection("carts").document(doc.id).delete()

    deleted_cart_items = 0
    for cart_id in cart_ids:
        item_docs = db.collection("cart_items").where("cart_id", "==", cart_id).stream()
        for item_doc in item_docs:
            db.collection("cart_items").document(item_doc.id).delete()
            deleted_cart_items += 1

    # 3. Documento do cliente
    customer_ref = db.collection("customers").document(whatsapp_phone)
    customer_existed = customer_ref.get().exists
    if customer_existed:
        customer_ref.delete()

    return {
        "orders_deleted": len(order_doc_ids),
        "order_items_deleted": deleted_order_items,
        "carts_deleted": len(cart_ids),
        "cart_items_deleted": deleted_cart_items,
        "customer_deleted": 1 if customer_existed else 0,
    }


def delete_order_firestore(order_id: int) -> bool:
    """Remove pedido e seus itens do Firestore."""
    db = get_firestore()

    order_docs = db.collection("orders").where("id", "==", order_id).limit(1).stream()
    order_doc_id: str | None = None
    for doc in order_docs:
        order_doc_id = doc.id
        break
    if not order_doc_id:
        return False

    item_docs = db.collection("order_items").where("order_id", "==", order_id).stream()
    for item_doc in item_docs:
        db.collection("order_items").document(item_doc.id).delete()

    db.collection("orders").document(order_doc_id).delete()
    return True
