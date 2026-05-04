"""ADK tools that agents can call to interact with the Diotex backend."""
from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
from datetime import datetime
from typing import Any
import unicodedata

import httpx
from google.adk.tools import ToolContext
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_db_session
from app.models import Cart, CartItem, Inventory, Order, OrderItem, OrderStatus, Product, ProductImage
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
                pool_stmt = select(Product, Inventory).outerjoin(
                    Inventory, Inventory.product_id == Product.id
                ).where(Product.active == True)  # noqa: E712
                pool = session.exec(pool_stmt).all()

                scored: list[tuple[float, tuple[Product, Inventory | None]]] = []
                for product, inv in pool:
                    normalized_name = _normalize_search_text(product.name or "")
                    if not normalized_name:
                        continue

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

        return {
            "success": len(errors) == 0,
            "found": len(items),
            "sent_images": sent_images,
            "sent_links": sent_links,
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


async def confirm_and_generate_pix(
    whatsapp_phone: str,
    address_number: str,
    tool_context: ToolContext,
    zipcode: str = "",
    customer_name: str = "",
    customer_email: str = "",
) -> dict[str, Any]:
    """Fecha o carrinho, cria o pedido e gera o código PIX para pagamento.

    Chamar esta tool APENAS quando o cliente disser explicitamente que quer fechar/pagar.
    Os campos zipcode, customer_name e customer_email podem ser omitidos se já foram
    informados anteriormente na conversa (serão recuperados do estado da sessão).

    Args:
        whatsapp_phone: Número do WhatsApp do cliente.
        address_number: Número da casa ou comércio para entrega.
        zipcode: CEP de entrega (usa o da sessão se omitido).
        customer_name: Nome completo do cliente (usa o da sessão se omitido).
        customer_email: E-mail do cliente (necessário para gerar PIX no MP).

    Returns:
        Código PIX copia-e-cola, valor total e ID do pedido.
    """
    # Recupera dados do estado da sessão como fallback
    zipcode = zipcode or tool_context.state.get("shipping_cep", "")
    customer_name = customer_name or tool_context.state.get("customer_name", "")
    customer_email = customer_email or tool_context.state.get("customer_email", "")

    if not zipcode:
        return {"error": "CEP de entrega nao informado."}
    if not customer_name:
        return {"error": "Nome do cliente nao informado."}
    if not customer_email:
        return {"error": "E-mail do cliente nao informado."}

    # Persiste na sessão para uso futuro
    tool_context.state["customer_name"] = customer_name
    tool_context.state["customer_email"] = customer_email
    tool_context.state["shipping_cep"] = zipcode

    from app.services.shipping import calculate_shipping_quote
    from app.schemas import ShippingProvider, ShippingQuoteRequest
    from app.services.mercadopago import create_pix_payment

    async def _retry_async(op_name: str, fn, attempts: int = 3) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts:
                    await asyncio.sleep(0.7 * attempt)
        if last_exc is not None:
            raise RuntimeError(f"{op_name} falhou apos {attempts} tentativas: {last_exc}") from last_exc
        raise RuntimeError(f"{op_name} falhou apos {attempts} tentativas")

    async def _notify_admin_failure(stage: str, reason: str, order_id: int | None = None) -> None:
        msg = (
            "⚠️ *Falha no checkout automatizado*\n"
            f"Etapa: {stage}\n"
            f"Cliente: {customer_name}\n"
            f"WhatsApp: {whatsapp_phone}\n"
            f"CEP: {zipcode}\n"
            f"Pedido: {order_id if order_id else '-'}\n"
            f"Motivo: {reason}\n"
            "\nAção recomendada: entrar em contato com o cliente para concluir manualmente."
        )
        try:
            await send_whatsapp_message(to_phone=settings.notification_phone, text=msg)
        except Exception:
            # Não interrompe o fluxo caso a notificação do admin também falhe.
            pass

    try:
        with get_db_session() as session:
            # Atualiza dados do cliente
            customer = upsert_customer(session, CustomerUpsert(
                whatsapp_phone=whatsapp_phone,
                name=customer_name,
                email=customer_email,
                zipcode=zipcode,
            ))
            customer.address_number = address_number
            session.add(customer)
            session.commit()
            session.refresh(customer)

            # Pega carrinho aberto
            cart = session.exec(
                select(Cart).where(Cart.customer_id == customer.id, Cart.status == "open")
            ).first()
            if not cart:
                return {"error": "Carrinho vazio. Adicione produtos antes de fechar o pedido."}

            cart_items = session.exec(select(CartItem).where(CartItem.cart_id == cart.id)).all()
            if not cart_items:
                return {"error": "Carrinho vazio."}

            subtotal = round(sum(i.line_total for i in cart_items), 2)

            # Usa peso/dimensões do primeiro produto como base (simplificado)
            first_product = session.get(Product, cart_items[0].product_id)
            # Snapshot dos dados para usar fora do context manager
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
            weight_g = first_product.weight_g if first_product and first_product.weight_g else 500.0
            length_cm = first_product.package_length_cm if first_product and first_product.package_length_cm else 30.0
            width_cm = first_product.package_width_cm if first_product and first_product.package_width_cm else 20.0
            height_cm = first_product.package_height_cm if first_product and first_product.package_height_cm else 5.0
            customer_id = customer.id
            cart_id = cart.id

        # Calcula frete (fora do db session)
        async def _shipping_call() -> Any:
            return await calculate_shipping_quote(ShippingQuoteRequest(
                provider=ShippingProvider.melhor_envio,
                to_zipcode=zipcode,
                product_name="Carrinho Diotex",
                quantity=1,
                unit_price=subtotal,
                weight_g=weight_g,
                package_length_cm=length_cm,
                package_width_cm=width_cm,
                package_height_cm=height_cm,
            ))

        shipping_resp = await _retry_async("cotacao de frete", _shipping_call, attempts=3)
        best_shipping = shipping_resp.options[0] if shipping_resp.options else None
        if not best_shipping:
            await _notify_admin_failure(
                stage="cotacao_frete",
                reason="Nao retornou opcoes de frete apos 3 tentativas.",
            )
            return {
                "error": (
                    "Nao consegui calcular o frete agora. Ja encaminhei seu atendimento para um especialista "
                    "humano e vamos continuar por aqui em instantes."
                )
            }
        shipping_amount = round(best_shipping.price if best_shipping else 0.0, 2)
        total_amount = round(subtotal + shipping_amount, 2)

        # Cria pedido e fecha carrinho
        with get_db_session() as session:
            order = Order(
                customer_id=customer_id,
                channel="whatsapp",
                shipping_zipcode=zipcode,
                subtotal_amount=subtotal,
                shipping_amount=shipping_amount,
                total_amount=total_amount,
                status=OrderStatus.awaiting_payment,
                payment_provider="mercadopago",
                shipping_quote_json=best_shipping.model_dump() if best_shipping else None,
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            order_id = order.id

            for ci in cart_items_data:
                session.add(OrderItem(
                    order_id=order_id,
                    product_id=ci["product_id"],
                    product_name_snapshot=ci["product_name_snapshot"],
                    unit_price_snapshot=ci["unit_price_snapshot"],
                    quantity=ci["quantity"],
                    line_total=ci["line_total"],
                ))

            # Fecha o carrinho
            cart_db = session.get(Cart, cart_id)
            if cart_db:
                cart_db.status = "closed"
                session.add(cart_db)
            session.commit()

        # Gera PIX no Mercado Pago
        async def _pix_call() -> dict[str, Any]:
            pix_result = await create_pix_payment(
                order_id=order_id,
                total_amount=total_amount,
                customer_name=customer_name,
                customer_email=customer_email,
            )
            if "error" in pix_result:
                raise RuntimeError(str(pix_result["error"]))
            return pix_result

        try:
            pix = await _retry_async("geracao PIX", _pix_call, attempts=3)
        except Exception as pix_exc:  # noqa: BLE001
            await _notify_admin_failure(
                stage="geracao_pix",
                reason=str(pix_exc),
                order_id=order_id,
            )
            return {
                "order_id": order_id,
                "total_amount": total_amount,
                "subtotal": subtotal,
                "shipping_amount": shipping_amount,
                "error": (
                    "Seu pedido foi registrado, mas tivemos instabilidade ao gerar o PIX agora. "
                    "Ja encaminhei seu atendimento para nosso administrador concluir com prioridade."
                ),
            }

        # Salva referência do pagamento no pedido
        with get_db_session() as session:
            order_db = session.get(Order, order_id)
            if order_db:
                order_db.payment_reference = str(pix.get("payment_id"))
                session.add(order_db)
                session.commit()

        return {
            "order_id": order_id,
            "total_amount": total_amount,
            "subtotal": subtotal,
            "shipping_amount": shipping_amount,
            "shipping_carrier": best_shipping.carrier_name if best_shipping else None,
            "pix_code": pix.get("qr_code"),
            "payment_id": pix.get("payment_id"),
            "expires_at": pix.get("expires_at"),
        }
    except Exception as exc:
        await _notify_admin_failure(stage="checkout_geral", reason=str(exc))
        return {
            "error": (
                "Tive uma falha tecnica para concluir seu checkout agora. "
                "Ja encaminhei seu atendimento para nosso administrador e vamos te retornar aqui."
            )
        }

