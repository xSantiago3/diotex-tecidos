"""ADK tools that agents can call to interact with the Diotex backend."""
from __future__ import annotations

from difflib import SequenceMatcher
from datetime import datetime
from typing import Any, Literal
import unicodedata

import httpx
from google.adk.tools import ToolContext
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_db_session
from app.models import Cart, CartItem, Customer, Inventory, Order, OrderItem, OrderStatus, Product, ProductImage
from app.repositories import get_order_for_customer, upsert_customer
from app.schemas import CheckoutItemRequest, CheckoutQuoteRequest, CustomerUpsert, ShippingProvider, ShippingQuoteRequest
from app.services.checkout import create_checkout_quote
from app.services.security import assert_admin_phone, start_admin_otp, verify_admin_otp
from app.services.shipping import calculate_shipping_quote
from app.services.whatsapp import send_whatsapp_image, send_whatsapp_message
from app.utils.phones import normalize_phone

settings = get_settings()

_BASE = "http://localhost:8080"


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower())
    clean_chars: list[str] = []
    for ch in normalized:
        if unicodedata.category(ch) == "Mn":
            continue
        clean_chars.append(ch if ch.isalnum() else " ")
    return " ".join("".join(clean_chars).split())


def _normalized_tokens(value: str) -> list[str]:
    """Normaliza tokens e inclui uma forma singular simples para buscas tolerantes."""
    base_tokens = _normalize_search_text(value).split()
    normalized: list[str] = []
    for token in base_tokens:
        if token not in normalized:
            normalized.append(token)
        if token.endswith("s") and len(token) > 3:
            singular = token[:-1]
            if singular not in normalized:
                normalized.append(singular)
    return normalized


def _build_product_url(slug: str | None) -> str | None:
    if not settings.woocommerce_base_url or not slug:
        return None
    return f"{settings.woocommerce_base_url.rstrip('/')}/produto/{slug}/"


def _sync_get(path: str, params: dict | None = None) -> dict[str, Any]:
    try:
        r = httpx.get(f"{_BASE}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


def _sync_post(path: str, body: dict) -> dict[str, Any]:
    try:
        r = httpx.post(f"{_BASE}{path}", json=body, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def list_product_categories() -> dict[str, Any]:
    """Lista as categorias de tecidos disponíveis na loja.

    Use esta tool quando o cliente perguntar quais tipos de tecido, quais categorias
    ou o que a loja vende em geral. Retorna nomes limpos como 'Oxford', 'Helanca', etc.

    Returns:
        Lista de categorias disponíveis no catálogo ativo.
    """
    try:
        with get_db_session() as session:
            from sqlmodel import distinct as sql_distinct
            rows = session.exec(
                select(sql_distinct(Product.categories))
                .where(Product.active == True, Product.categories != None)  # noqa: E712
            ).all()

            seen: set[str] = set()
            for raw in rows:
                for part in raw.split(","):
                    part = part.strip()
                    # Remove prefixo "Tecidos > " e "Todos"
                    if " > " in part:
                        part = part.split(" > ", 1)[1].strip()
                    if part and part.lower() != "todos":
                        seen.add(part)

            return {"categories": sorted(seen)}
    except Exception as exc:
        return {"error": str(exc)}


def search_products(query: str = "", limit: int = 10) -> dict[str, Any]:
    """Busca produtos no catálogo da loja pelo nome ou palavra-chave.

    Args:
        query: Palavra-chave para buscar (ex: 'linho', 'seda'). Deixe vazio para listar tudo.
        limit: Quantidade máxima de resultados (padrão 10).

    Returns:
        Dicionário com lista de produtos e total encontrado.
    """
    try:
        with get_db_session() as session:
            stmt = select(Product, Inventory).outerjoin(
                Inventory, Inventory.product_id == Product.id
            ).where(Product.active == True)  # noqa: E712
            if query:
                stmt = stmt.where(Product.name.ilike(f"%{query}%"))
            stmt = stmt.limit(limit)
            rows = session.exec(stmt).all()

            # Fallback tolerante a typos quando busca textual nao retornar resultados.
            if query and not rows:
                normalized_query = _normalize_search_text(query)
                query_tokens = _normalized_tokens(query)
                pool_stmt = select(Product, Inventory).outerjoin(
                    Inventory, Inventory.product_id == Product.id
                ).where(Product.active == True)  # noqa: E712
                pool = session.exec(pool_stmt).all()

                scored: list[tuple[float, tuple[Product, Inventory | None]]] = []
                for product, inv in pool:
                    normalized_name = _normalize_search_text(product.name or "")
                    if not normalized_name:
                        continue
                    name_tokens = _normalized_tokens(product.name or "")

                    score = SequenceMatcher(None, normalized_query, normalized_name).ratio()
                    if normalized_query in normalized_name:
                        score = max(score, 0.95)
                    else:
                        name_parts = normalized_name.split()
                        if name_parts:
                            part_score = max(
                                SequenceMatcher(None, normalized_query, part).ratio()
                                for part in name_parts
                            )
                            score = max(score, part_score)

                    # Bônus por cobertura de tokens (ex.: "helancas rosa" -> "helanca rosa pink").
                    token_hits = 0
                    if query_tokens and name_tokens:
                        for qtok in query_tokens:
                            if any(
                                (
                                    qtok == ntok
                                    or ntok.startswith(qtok)
                                    or qtok.startswith(ntok)
                                )
                                and min(len(qtok), len(ntok)) >= 4
                                for ntok in name_tokens
                            ):
                                token_hits += 1

                    if query_tokens:
                        required_hits = max(1, len(query_tokens) - 1)
                        if token_hits >= required_hits:
                            # Quanto mais tokens casar, maior a relevancia.
                            token_boost = min(0.98, 0.85 + (0.04 * token_hits))
                            score = max(score, token_boost)
                        elif token_hits >= 1:
                            score = max(score, 0.76)

                    # Para consultas com 2+ termos, evita falsos positivos sem sobreposição de tokens.
                    if len(query_tokens) >= 2 and token_hits == 0:
                        continue

                    # limiar conservador: corrige typo comum sem abrir para ruido.
                    if score >= 0.72:
                        scored.append((score, (product, inv)))

                scored.sort(key=lambda item: item[0], reverse=True)
                rows = [item[1] for item in scored[:limit]]

            items = []
            for product, inv in rows:
                image_rows = session.exec(
                    select(ProductImage)
                    .where(ProductImage.product_id == product.id)
                    .order_by(ProductImage.sort_order.asc())
                ).all()
                items.append({
                    "id": product.id,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "unit_type": product.unit_type,
                    "composition": product.composition,
                    "color": product.color,
                    "categories": product.categories,
                    "available_quantity": inv.available_quantity if inv else None,
                    "description": product.short_description or product.description,
                    "product_url": _build_product_url(product.slug),
                    "image_urls": [img.source_url for img in image_rows if img.source_url],
                })
            return {"items": items, "total": len(items)}
    except Exception as exc:
        return {"error": str(exc)}


async def send_catalog_media(
    whatsapp_phone: str,
    query: str,
    tool_context: ToolContext,
    include_images: bool = True,
    include_links: bool = False,
    limit: int = 8,
) -> dict[str, Any]:
    """Envia midia de catalogo (fotos e/ou links) para o WhatsApp do cliente.

    Tool para uso quando o cliente pedir para ver produtos, fotos, imagens, opcoes visuais
    ou links do catalogo. Esta tool ja busca os produtos e faz o envio no WhatsApp.

    Args:
        whatsapp_phone: Numero WhatsApp do cliente em formato internacional.
        query: Termo de busca do produto (ex: 'helanca verde').
        include_images: Se true, envia imagens dos produtos encontrados.
        include_links: Se true, envia links das paginas dos produtos encontrados.
        limit: Quantidade maxima de produtos para envio (1-20).

    Returns:
        Resumo com quantidade de produtos encontrados e quantos envios foram realizados.
    """
    try:
        safe_limit = max(1, min(limit, 20))
        result = search_products(query=query, limit=safe_limit)
        if result.get("error"):
            return result

        items = result.get("items", [])
        if not items:
            return {
                "success": False,
                "found": 0,
                "sent_images": 0,
                "sent_links": 0,
                "message": "Nenhum produto encontrado para envio de midia.",
            }

        sent_images = 0
        sent_links = 0
        errors: list[str] = []
        sent_item_names: list[str] = []

        for item in items:
            product_name = item.get("name") or "Produto"
            image_urls = item.get("image_urls") or []
            product_url = item.get("product_url")

            if include_images and image_urls:
                image_result = await send_whatsapp_image(
                    to_phone=whatsapp_phone,
                    image_url=image_urls[0],
                    caption=product_name,
                )
                if isinstance(image_result, dict) and image_result.get("error"):
                    errors.append(
                        f"Erro ao enviar imagem de {product_name}: {image_result.get('response') or image_result.get('error')}"
                    )
                else:
                    sent_images += 1
                    if product_name not in sent_item_names:
                        sent_item_names.append(product_name)

            if include_links and product_url:
                link_result = await send_whatsapp_message(
                    to_phone=whatsapp_phone,
                    text=f"{product_name}\n{product_url}",
                )
                if isinstance(link_result, dict) and link_result.get("error"):
                    errors.append(
                        f"Erro ao enviar link de {product_name}: {link_result.get('response') or link_result.get('error')}"
                    )
                else:
                    sent_links += 1
                    if product_name not in sent_item_names:
                        sent_item_names.append(product_name)

        tool_context.state["last_media_query"] = query
        tool_context.state["last_media_sent_items"] = sent_item_names
        tool_context.state["last_media_include_images"] = include_images
        tool_context.state["last_media_include_links"] = include_links

        return {
            "success": len(errors) == 0,
            "found": len(items),
            "sent_images": sent_images,
            "sent_links": sent_links,
            "sent_item_names": sent_item_names,
            "errors": errors,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------

async def quote_shipping(
    to_zipcode: str,
    product_name: str,
    tool_context: ToolContext,
    quantity: float = 1.0,
    unit_price: float = 10.0,
    weight_g: float = 300.0,
    package_length_cm: float = 30.0,
    package_width_cm: float = 20.0,
    package_height_cm: float = 5.0,
) -> dict[str, Any]:
    """Calcula opções de frete para um CEP de destino.

    Args:
        to_zipcode: CEP de destino (somente dígitos ou com hífen).
        product_name: Nome do produto principal do pedido.
        quantity: Quantidade de itens.
        unit_price: Preço unitário em reais.
        weight_g: Peso em gramas.
        package_length_cm: Comprimento da embalagem em cm.
        package_width_cm: Largura da embalagem em cm.
        package_height_cm: Altura da embalagem em cm.

    Returns:
        Lista de opções de frete com transportadora, prazo e preço.
    """
    try:
        req = ShippingQuoteRequest(
            provider=ShippingProvider.melhor_envio,
            to_zipcode=to_zipcode,
            product_name=product_name,
            quantity=int(quantity),
            unit_price=unit_price,
            weight_g=weight_g,
            package_length_cm=package_length_cm,
            package_width_cm=package_width_cm,
            package_height_cm=package_height_cm,
        )
        result = await calculate_shipping_quote(req)
        # Persiste o CEP na sessão para o payment_agent reutilizar
        tool_context.state["shipping_cep"] = to_zipcode
        return {"options": [o.model_dump() for o in result.options]}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

async def create_order_quote(
    whatsapp_phone: str,
    zipcode: str,
    product_ids_and_quantities: list[dict],
    customer_name: str | None = None,
) -> dict[str, Any]:
    """Cria uma cotação de pedido com subtotal, frete e total final.

    Args:
        whatsapp_phone: Número do WhatsApp do cliente (com DDI, ex: '5511999999999').
        zipcode: CEP de entrega.
        product_ids_and_quantities: Lista de dicts com 'product_id' (int) e 'quantity' (float).
            Exemplo: [{"product_id": 1, "quantity": 2.0}]
        customer_name: Nome do cliente (opcional).

    Returns:
        Cotação com itens, subtotal, frete e total.
    """
    try:
        items = [CheckoutItemRequest(**item) for item in product_ids_and_quantities]
        req = CheckoutQuoteRequest(
            whatsapp_phone=whatsapp_phone,
            customer_name=customer_name,
            zipcode=zipcode,
            items=items,
        )
        with get_db_session() as session:
            result = await create_checkout_quote(session, req)
        return result.model_dump(mode="json")
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Support — consulta de pedidos
# ---------------------------------------------------------------------------

def get_order_status(whatsapp_phone: str, order_id: int) -> dict[str, Any]:
    """Consulta o status de um pedido do cliente.

    O pedido só é retornado se pertencer ao número informado (segurança).

    Args:
        whatsapp_phone: Número do WhatsApp do cliente remetente da mensagem.
        order_id: ID do pedido a consultar.

    Returns:
        Status, itens, valores e datas do pedido.
    """
    try:
        with get_db_session() as session:
            result = get_order_for_customer(session, whatsapp_phone, order_id)
            return result.model_dump(mode="json")
    except Exception as exc:
        return {"error": str(exc)}


def list_my_orders(whatsapp_phone: str) -> dict[str, Any]:
    """Lista todos os pedidos do cliente identificado pelo número de WhatsApp.

    Args:
        whatsapp_phone: Número do WhatsApp do cliente.

    Returns:
        Lista com id, status, total e data de cada pedido.
    """
    try:
        with get_db_session() as session:
            from app.repositories import get_customer_by_phone
            customer = get_customer_by_phone(session, whatsapp_phone)
            if not customer:
                return {"error": "Cliente nao encontrado."}
            orders = session.exec(
                select(Order).where(Order.customer_id == customer.id).order_by(Order.created_at.desc()).limit(10)
            ).all()
            return {
                "orders": [
                    {
                        "order_id": o.id,
                        "status": o.status,
                        "total_amount": o.total_amount,
                        "created_at": o.created_at.isoformat() if o.created_at else None,
                    }
                    for o in orders
                ]
            }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Admin — modificações protegidas por OTP
# ---------------------------------------------------------------------------

def request_admin_otp(whatsapp_phone: str, purpose: str = "admin") -> dict[str, Any]:
    """Inicia o fluxo OTP para operações administrativas.

    Só funciona para números cadastrados em ADMIN_ALLOWED_PHONES.
    Após chamar esta tool, o admin receberá um código OTP por WhatsApp ou deverá digitá-lo.

    Args:
        whatsapp_phone: Número do WhatsApp de quem está solicitando (deve ser admin).
        purpose: Identificador da operação (ex: 'update_price', 'update_stock').

    Returns:
        Código OTP gerado (para ser enviado ao admin) ou erro de permissão.
    """
    try:
        with get_db_session() as session:
            from app.services.security import build_otp_code
            challenge = start_admin_otp(session, whatsapp_phone, purpose)
            otp_code = build_otp_code(challenge)
            return {"otp_code": otp_code, "expires_minutes": 10, "purpose": purpose}
    except Exception as exc:
        return {"error": str(exc)}


def update_product_price(
    whatsapp_phone: str,
    otp_code: str,
    product_id: int,
    new_price: float,
) -> dict[str, Any]:
    """Atualiza o preço de um produto após validação do OTP admin.

    Args:
        whatsapp_phone: Número do WhatsApp do admin solicitante.
        otp_code: Código OTP recebido via request_admin_otp.
        product_id: ID do produto a atualizar.
        new_price: Novo preço em reais.

    Returns:
        Confirmação com nome do produto e novo preço.
    """
    try:
        with get_db_session() as session:
            verify_admin_otp(session, whatsapp_phone, "update_price", otp_code)
            product = session.get(Product, product_id)
            if not product:
                return {"error": f"Produto {product_id} nao encontrado."}
            old_price = product.price
            product.price = new_price
            session.add(product)
            session.commit()
            return {"success": True, "product": product.name, "old_price": old_price, "new_price": new_price}
    except Exception as exc:
        return {"error": str(exc)}


def update_product_stock(
    whatsapp_phone: str,
    otp_code: str,
    product_id: int,
    new_quantity: float,
) -> dict[str, Any]:
    """Atualiza o estoque disponível de um produto após validação do OTP admin.

    Args:
        whatsapp_phone: Número do WhatsApp do admin solicitante.
        otp_code: Código OTP recebido via request_admin_otp.
        product_id: ID do produto a atualizar.
        new_quantity: Nova quantidade disponível em estoque.

    Returns:
        Confirmação com nome do produto e novo estoque.
    """
    try:
        with get_db_session() as session:
            verify_admin_otp(session, whatsapp_phone, "update_stock", otp_code)
            product = session.get(Product, product_id)
            if not product:
                return {"error": f"Produto {product_id} nao encontrado."}
            inventory = session.exec(select(Inventory).where(Inventory.product_id == product_id)).first()
            if not inventory:
                return {"error": f"Estoque nao encontrado para o produto {product_id}."}
            old_qty = inventory.available_quantity
            inventory.available_quantity = new_quantity
            session.add(inventory)
            session.commit()
            return {"success": True, "product": product.name, "old_quantity": old_qty, "new_quantity": new_quantity}
    except Exception as exc:
        return {"error": str(exc)}


def confirm_order_payment(
    whatsapp_phone: str,
    otp_code: str,
    order_id: int,
) -> dict[str, Any]:
    """Confirma manualmente um pagamento pendente usando OTP administrativo.

    Use quando um admin confirmar que o PIX caiu ou quiser marcar um pedido
    pendente como pago manualmente.
    """
    try:
        with get_db_session() as session:
            verify_admin_otp(session, whatsapp_phone, "confirm_payment", otp_code)

        result = _sync_post(
            f"/internal/orders/{order_id}/confirm-payment",
            {"approved_by": normalize_phone(whatsapp_phone)},
        )
        if result.get("error"):
            return result
        return {
            "success": bool(result.get("confirmed")),
            "order_id": order_id,
            "status": result.get("status"),
            "message": (
                f"Pedido #{order_id} confirmado manualmente como pago."
                if result.get("confirmed")
                else result.get("message", "Nao foi possivel confirmar o pagamento.")
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Carrinho persistente
# ---------------------------------------------------------------------------

def _get_or_create_cart(session: Session, customer_id: int) -> Cart:
    cart = session.exec(
        select(Cart).where(Cart.customer_id == customer_id, Cart.status == "open")
    ).first()
    if not cart:
        cart = Cart(customer_id=customer_id)
        session.add(cart)
        session.commit()
        session.refresh(cart)
    return cart


def _cart_summary(session: Session, cart: Cart) -> dict[str, Any]:
    items = session.exec(select(CartItem).where(CartItem.cart_id == cart.id)).all()
    subtotal = sum(i.line_total for i in items)
    return {
        "cart_id": cart.id,
        "items": [
            {
                "product_id": i.product_id,
                "name": i.product_name_snapshot,
                "quantity": i.quantity,
                "unit_price": i.unit_price_snapshot,
                "line_total": i.line_total,
            }
            for i in items
        ],
        "subtotal": round(subtotal, 2),
        "item_count": len(items),
    }


def add_to_cart(whatsapp_phone: str, product_id: int, quantity: float, tool_context: ToolContext) -> dict[str, Any]:
    """Adiciona um produto ao carrinho do cliente.

    Após adicionar, retorna o resumo completo do carrinho para mostrar ao cliente.

    Args:
        whatsapp_phone: Número do WhatsApp do cliente remetente.
        product_id: ID do produto a adicionar.
        quantity: Quantidade (em metros ou unidades conforme o produto).

    Returns:
        Resumo do carrinho com todos os itens, quantidades e valores.
    """
    try:
        with get_db_session() as session:
            customer = upsert_customer(session, CustomerUpsert(whatsapp_phone=whatsapp_phone))
            product = session.get(Product, product_id)
            if not product or not product.active:
                return {"error": f"Produto {product_id} nao encontrado."}

            cart = _get_or_create_cart(session, customer.id)

            # Se já existe o produto no carrinho, soma a quantidade
            existing = session.exec(
                select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
            ).first()
            if existing:
                existing.quantity += quantity
                existing.line_total = round(existing.quantity * existing.unit_price_snapshot, 2)
                session.add(existing)
            else:
                line_total = round(product.price * quantity, 2)
                item = CartItem(
                    cart_id=cart.id,
                    product_id=product_id,
                    product_name_snapshot=product.name,
                    unit_price_snapshot=product.price,
                    quantity=quantity,
                    line_total=line_total,
                )
                session.add(item)

            cart.updated_at = datetime.utcnow()
            session.add(cart)
            session.commit()
            session.refresh(cart)
            summary = _cart_summary(session, cart)

        # Persiste dados relevantes na sessão ADK
        tool_context.state["customer_phone"] = whatsapp_phone
        tool_context.state["cart_summary"] = summary
        return summary
    except Exception as exc:
        return {"error": str(exc)}


def view_cart(whatsapp_phone: str, tool_context: ToolContext) -> dict[str, Any]:
    """Retorna o conteúdo atual do carrinho do cliente.

    Args:
        whatsapp_phone: Número do WhatsApp do cliente.

    Returns:
        Resumo do carrinho com itens, quantidades e subtotal.
    """
    try:
        with get_db_session() as session:
            customer = upsert_customer(session, CustomerUpsert(whatsapp_phone=whatsapp_phone))
            cart = session.exec(
                select(Cart).where(Cart.customer_id == customer.id, Cart.status == "open")
            ).first()
            if not cart:
                return {"cart_id": None, "items": [], "subtotal": 0.0, "item_count": 0}
            summary = _cart_summary(session, cart)

        tool_context.state["customer_phone"] = whatsapp_phone
        tool_context.state["cart_summary"] = summary
        return summary
    except Exception as exc:
        return {"error": str(exc)}


def remove_from_cart(whatsapp_phone: str, product_id: int, tool_context: ToolContext) -> dict[str, Any]:
    """Remove um produto do carrinho do cliente.

    Args:
        whatsapp_phone: Número do WhatsApp do cliente.
        product_id: ID do produto a remover.

    Returns:
        Resumo atualizado do carrinho.
    """
    try:
        with get_db_session() as session:
            customer = upsert_customer(session, CustomerUpsert(whatsapp_phone=whatsapp_phone))
            cart = session.exec(
                select(Cart).where(Cart.customer_id == customer.id, Cart.status == "open")
            ).first()
            if not cart:
                return {"error": "Carrinho vazio."}
            item = session.exec(
                select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id)
            ).first()
            if item:
                session.delete(item)
                session.commit()
            summary = _cart_summary(session, cart)

        tool_context.state["cart_summary"] = summary
        return summary
    except Exception as exc:
        return {"error": str(exc)}


def get_checkout_customer_profile(whatsapp_phone: str, tool_context: ToolContext) -> dict[str, Any]:
    """Retorna dados salvos do comprador para confirmar antes de gerar pedido.

    Use esta tool no início do checkout para perguntar:
    "Esses continuam sendo seus dados?"
    """
    try:
        normalized_phone = normalize_phone(whatsapp_phone)
        with get_db_session() as session:
            customer = session.exec(
                select(Customer).where(Customer.whatsapp_phone == normalized_phone)
            ).first()

        if not customer:
            return {
                "has_saved_data": False,
                "customer_profile": {
                    "zipcode": "",
                    "full_name": "",
                    "email": "",
                    "cpf": "",
                    "address_number": "",
                },
                "missing_fields": ["zipcode", "full_name", "email", "cpf", "address_number"],
                "confirmation_prompt": (
                    "Ainda nao encontrei dados salvos seus. "
                    "Me informe CEP, nome completo, e-mail, CPF e numero da casa."
                ),
            }

        profile = {
            "zipcode": customer.zipcode or "",
            "full_name": customer.name or "",
            "email": customer.email or "",
            "cpf": customer.cpf or "",
            "address_number": customer.address_number or "",
        }
        missing_fields = [field for field, value in profile.items() if not value]

        tool_context.state["shipping_cep"] = profile["zipcode"]
        tool_context.state["customer_name"] = profile["full_name"]
        tool_context.state["customer_email"] = profile["email"]
        tool_context.state["customer_cpf"] = profile["cpf"]
        tool_context.state["customer_address_number"] = profile["address_number"]

        return {
            "has_saved_data": True,
            "customer_profile": profile,
            "missing_fields": missing_fields,
            "confirmation_prompt": (
                "Esses continuam sendo seus dados?\\n"
                f"CEP: {profile['zipcode'] or '-'}\\n"
                f"Nome Completo: {profile['full_name'] or '-'}\\n"
                f"email: {profile['email'] or '-'}\\n"
                f"CPF: {profile['cpf'] or '-'}\\n"
                f"Numero: {profile['address_number'] or '-'}"
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _build_open_cart_checkout_snapshot(
    whatsapp_phone: str,
    zipcode: str,
    customer_name: str,
    customer_email: str,
    customer_cpf: str,
    address_number: str,
) -> dict[str, Any]:
    with get_db_session() as session:
        customer = upsert_customer(session, CustomerUpsert(
            whatsapp_phone=whatsapp_phone,
            name=customer_name or None,
            email=customer_email or None,
            cpf=customer_cpf or None,
            zipcode=zipcode or None,
        ))
        if address_number:
            customer.address_number = address_number
            session.add(customer)
            session.commit()
            session.refresh(customer)

        cart = session.exec(
            select(Cart).where(Cart.customer_id == customer.id, Cart.status == "open")
        ).first()
        if not cart:
            return {"error": "Carrinho vazio. Adicione produtos antes de fechar o pedido."}

        cart_items = session.exec(select(CartItem).where(CartItem.cart_id == cart.id)).all()
        if not cart_items:
            return {"error": "Carrinho vazio."}

        subtotal = round(sum(i.line_total for i in cart_items), 2)

        products: list[Product] = []
        for ci in cart_items:
            product = session.get(Product, ci.product_id)
            if not product:
                return {"error": f"Produto {ci.product_id} nao encontrado no catalogo."}
            products.append(product)

        total_weight_g = 0.0
        max_length_cm = 0.0
        max_width_cm = 0.0
        total_height_cm = 0.0
        for product, ci in zip(products, cart_items):
            if not all([
                product.weight_g,
                product.package_length_cm,
                product.package_width_cm,
                product.package_height_cm,
            ]):
                return {"error": f"Produto sem peso/dimensoes cadastrados: {product.name}"}
            total_weight_g += (product.weight_g or 0.0) * ci.quantity
            max_length_cm = max(max_length_cm, product.package_length_cm or 0.0)
            max_width_cm = max(max_width_cm, product.package_width_cm or 0.0)
            total_height_cm += (product.package_height_cm or 0.0) * ci.quantity

        cart_items_data = [
            {
                "product_id": ci.product_id,
                "product_name_snapshot": ci.product_name_snapshot,
                "unit_price_snapshot": ci.unit_price_snapshot,
                "quantity": ci.quantity,
                "line_total": ci.line_total,
            }
            for ci in cart_items
        ]

        return {
            "customer_id": customer.id,
            "cart_id": cart.id,
            "subtotal": subtotal,
            "cart_items_data": cart_items_data,
            "weight_g": total_weight_g,
            "length_cm": max_length_cm,
            "width_cm": max_width_cm,
            "height_cm": max(total_height_cm, 1.0),
        }


async def prepare_checkout_options(
    whatsapp_phone: str,
    zipcode: str,
    tool_context: ToolContext,
    address_number: str = "",
    customer_name: str = "",
    customer_email: str = "",
    customer_cpf: str = "",
) -> dict[str, Any]:
    """Calcula fretes do Melhor Envio e apresenta opções para o cliente escolher.

    Esta tool deve ser chamada antes de fechar o pedido e antes de definir o método
    de pagamento. O agente deve mostrar as opções e pedir que o cliente escolha.
    """
    from app.schemas import ShippingProvider, ShippingQuoteRequest

    zipcode = zipcode or tool_context.state.get("shipping_cep", "")
    customer_name = customer_name or tool_context.state.get("customer_name", "")
    customer_email = customer_email or tool_context.state.get("customer_email", "")
    customer_cpf = customer_cpf or tool_context.state.get("customer_cpf", "")
    address_number = address_number or tool_context.state.get("customer_address_number", "")

    if not zipcode:
        return {"error": "CEP de entrega nao informado."}

    snapshot = _build_open_cart_checkout_snapshot(
        whatsapp_phone=whatsapp_phone,
        zipcode=zipcode,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_cpf=customer_cpf,
        address_number=address_number,
    )
    if snapshot.get("error"):
        return snapshot

    shipping_resp = await calculate_shipping_quote(ShippingQuoteRequest(
        provider=ShippingProvider.melhor_envio,
        to_zipcode=zipcode,
        product_name="Carrinho Diotex",
        quantity=1,
        unit_price=snapshot["subtotal"],
        weight_g=snapshot["weight_g"],
        package_length_cm=snapshot["length_cm"],
        package_width_cm=snapshot["width_cm"],
        package_height_cm=snapshot["height_cm"],
        insurance_value=snapshot["subtotal"],
    ))
    if not shipping_resp.options:
        return {"error": "Nao foi possivel obter opcoes de envio para este CEP."}

    options = [opt.model_dump() for opt in shipping_resp.options]
    previews = []
    for idx, opt in enumerate(shipping_resp.options):
        previews.append({
            "option_index": idx,
            "carrier": opt.carrier_name,
            "service_name": opt.service_name,
            "service_code": opt.service_code,
            "shipping_amount": round(opt.price, 2),
            "delivery_days": opt.delivery_days,
            "delivery_days_with_preparation": opt.delivery_days_with_preparation,
            "total_amount": round(snapshot["subtotal"] + opt.price, 2),
        })

    tool_context.state["customer_phone"] = whatsapp_phone
    tool_context.state["shipping_cep"] = zipcode
    tool_context.state["customer_name"] = customer_name
    tool_context.state["customer_email"] = customer_email
    tool_context.state["customer_cpf"] = customer_cpf
    tool_context.state["customer_address_number"] = address_number
    tool_context.state["checkout_shipping_options"] = options
    tool_context.state["checkout_cart_snapshot"] = snapshot

    return {
        "subtotal": snapshot["subtotal"],
        "shipping_options": previews,
        "message": "Mostre as opcoes ao cliente e peca para escolher o option_index desejado.",
    }


async def finalize_checkout_payment(
    whatsapp_phone: str,
    payment_method: Literal["pix", "mercado_pago"],
    shipping_option_index: int,
    tool_context: ToolContext,
    address_number: str = "",
    zipcode: str = "",
    customer_name: str = "",
    customer_email: str = "",
    customer_cpf: str = "",
) -> dict[str, Any]:
    """Fecha o pedido após escolha do frete e método de pagamento.

    Fluxo esperado:
    1. Chamar prepare_checkout_options
    2. Cliente escolhe envio (option_index)
    3. Chamar esta tool com payment_method=\"pix\" ou \"mercado_pago\"

    - pix: retorna chave PIX e valor (sem QR do Mercado Pago)
    - mercado_pago: cria link de pagamento e envia por WhatsApp ao cliente
    """
    from app.services.mercadopago import create_payment_link

    zipcode = zipcode or tool_context.state.get("shipping_cep", "")
    customer_name = customer_name or tool_context.state.get("customer_name", "")
    customer_email = customer_email or tool_context.state.get("customer_email", "")
    customer_cpf = customer_cpf or tool_context.state.get("customer_cpf", "")
    address_number = address_number or tool_context.state.get("customer_address_number", "")

    if not zipcode:
        return {"error": "CEP de entrega nao informado."}
    if not address_number:
        return {"error": "Numero da casa nao informado."}
    if not customer_name:
        return {"error": "Nome do cliente nao informado."}
    if not customer_cpf:
        return {"error": "CPF do cliente nao informado."}
    if payment_method == "mercado_pago" and not customer_email:
        return {"error": "E-mail do cliente nao informado para Mercado Pago."}

    options = tool_context.state.get("checkout_shipping_options")
    snapshot = tool_context.state.get("checkout_cart_snapshot")
    if not isinstance(options, list) or not options:
        return {"error": "Primeiro chame prepare_checkout_options para carregar opcoes de envio."}
    if not isinstance(snapshot, dict) or not snapshot.get("cart_items_data"):
        return {"error": "Contexto do carrinho ausente. Chame prepare_checkout_options novamente."}
    if shipping_option_index < 0 or shipping_option_index >= len(options):
        return {"error": f"shipping_option_index invalido. Escolha entre 0 e {len(options)-1}."}

    selected_shipping = options[shipping_option_index]
    shipping_amount = round(float(selected_shipping.get("price", 0.0)), 2)
    subtotal = round(float(snapshot.get("subtotal", 0.0)), 2)
    total_amount = round(subtotal + shipping_amount, 2)

    # Revalida carrinho aberto e dados atuais antes de fechar
    fresh_snapshot = _build_open_cart_checkout_snapshot(
        whatsapp_phone=whatsapp_phone,
        zipcode=zipcode,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_cpf=customer_cpf,
        address_number=address_number,
    )
    if fresh_snapshot.get("error"):
        return fresh_snapshot

    with get_db_session() as session:
        order_status = OrderStatus.payment_under_review if payment_method == "pix" else OrderStatus.awaiting_payment
        order = Order(
            customer_id=fresh_snapshot["customer_id"],
            channel="whatsapp",
            shipping_zipcode=zipcode,
            subtotal_amount=subtotal,
            shipping_amount=shipping_amount,
            total_amount=total_amount,
            status=order_status,
            payment_provider="pix_manual" if payment_method == "pix" else "mercadopago",
            shipping_quote_json=selected_shipping,
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        order_id = order.id

        for ci in fresh_snapshot["cart_items_data"]:
            session.add(OrderItem(
                order_id=order_id,
                product_id=ci["product_id"],
                product_name_snapshot=ci["product_name_snapshot"],
                unit_price_snapshot=ci["unit_price_snapshot"],
                quantity=ci["quantity"],
                line_total=ci["line_total"],
            ))

        cart_db = session.get(Cart, fresh_snapshot["cart_id"])
        if cart_db:
            cart_db.status = "closed"
            session.add(cart_db)
        session.commit()

    tool_context.state["last_order_id"] = order_id
    tool_context.state["customer_cpf"] = customer_cpf
    tool_context.state["customer_address_number"] = address_number

    if payment_method == "pix":
        pix_key = settings.pix_key or "PIX indisponivel"
        if settings.pix_key:
            await send_whatsapp_message(
                to_phone=whatsapp_phone,
                text=(
                    f"Pagamento via PIX selecionado.\n"
                    f"Pedido #{order_id}\n"
                    f"Valor total: R${total_amount:.2f}\n"
                    f"Chave PIX: {pix_key}"
                ),
            )

        _sync_post(f"/internal/orders/{order_id}/notify-pix-pending", {})
        return {
            "order_id": order_id,
            "payment_method": "pix",
            "order_status": "payment_pending",
            "payment_pending": True,
            "subtotal": subtotal,
            "shipping_amount": shipping_amount,
            "total_amount": total_amount,
            "pix_key": pix_key,
            "customer_message_hint": (
                "Pedido criado e aguardando pagamento via PIX. Nao diga que foi finalizado; "
                "informe que o pagamento ainda precisa ser realizado e confirmado."
            ),
            "shipping_selected": {
                "carrier": selected_shipping.get("carrier_name"),
                "service_name": selected_shipping.get("service_name"),
                "delivery_days_with_preparation": selected_shipping.get("delivery_days_with_preparation"),
            },
        }

    link_result = await create_payment_link(
        order_id=order_id,
        total_amount=total_amount,
        customer_name=customer_name,
        customer_email=customer_email,
        description=f"Pedido #{order_id} - Diotex Tecidos",
    )
    if link_result.get("error"):
        return {
            "order_id": order_id,
            "payment_method": "mercado_pago",
            "order_status": "payment_pending",
            "payment_pending": True,
            "subtotal": subtotal,
            "shipping_amount": shipping_amount,
            "total_amount": total_amount,
            "error": f"Pedido criado, mas nao foi possivel gerar link MP: {link_result['error']}",
            "customer_message_hint": (
                "Pedido criado, mas pagamento ainda nao concluido. Nao diga que foi finalizado."
            ),
        }

    payment_link = link_result.get("payment_link")
    if payment_link:
        await send_whatsapp_message(
            to_phone=whatsapp_phone,
            text=(
                f"Pagamento via Mercado Pago selecionado.\n"
                f"Pedido #{order_id}\n"
                f"Valor total: R${total_amount:.2f}\n"
                f"Link para pagamento: {payment_link}"
            ),
        )

    return {
        "order_id": order_id,
        "payment_method": "mercado_pago",
        "order_status": "payment_pending",
        "payment_pending": True,
        "subtotal": subtotal,
        "shipping_amount": shipping_amount,
        "total_amount": total_amount,
        "payment_link": payment_link,
        "customer_message_hint": (
            "O link de pagamento ja foi enviado ao cliente por WhatsApp. Nao envie nenhuma mensagem adicional agora. "
            "A confirmacao ao cliente deve acontecer apenas apos o webhook do Mercado Pago aprovar o pagamento."
        ),
        "suppress_agent_reply": True,
        "shipping_selected": {
            "carrier": selected_shipping.get("carrier_name"),
            "service_name": selected_shipping.get("service_name"),
            "delivery_days_with_preparation": selected_shipping.get("delivery_days_with_preparation"),
        },
    }


async def confirm_and_generate_pix(
    whatsapp_phone: str,
    address_number: str,
    tool_context: ToolContext,
    zipcode: str = "",
    customer_name: str = "",
    customer_email: str = "",
    customer_cpf: str = "",
) -> dict[str, Any]:
    """Compatibilidade retroativa: mantém assinatura antiga e usa PIX manual.

    Preferir usar prepare_checkout_options + finalize_checkout_payment.
    """
    prep = await prepare_checkout_options(
        whatsapp_phone=whatsapp_phone,
        zipcode=zipcode,
        tool_context=tool_context,
        address_number=address_number,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_cpf=customer_cpf,
    )
    if prep.get("error"):
        return prep

    return await finalize_checkout_payment(
        whatsapp_phone=whatsapp_phone,
        payment_method="pix",
        shipping_option_index=0,
        tool_context=tool_context,
        address_number=address_number,
        zipcode=zipcode,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_cpf=customer_cpf,
    )

