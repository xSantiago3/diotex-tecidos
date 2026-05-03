from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Product, ProductImage


def _strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"<[^>]+>", "", raw)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _parse_categories(categories: list[dict[str, Any]] | None) -> str | None:
    if not categories:
        return None
    names = [cat.get("name") for cat in categories if cat.get("name")]
    return ", ".join(names) if names else None


def _parse_images(images: list[dict[str, Any]] | None) -> list[str]:
    if not images:
        return []
    urls = [image.get("src") for image in images if image.get("src")]
    return urls


def _upsert_product_from_woo(session: Session, woo_product: dict[str, Any]) -> tuple[int, int]:
    woo_id = woo_product.get("id")
    product = session.exec(select(Product).where(Product.woo_product_id == woo_id)).first()

    attributes = woo_product.get("attributes", [])
    composition = None
    for attribute in attributes:
        name = str(attribute.get("name", "")).lower()
        if "compos" in name:
            options = attribute.get("options") or []
            if options:
                composition = ", ".join(options)

    payload = {
        "woo_product_id": woo_id,
        "product_type": woo_product.get("type"),
        "name": woo_product.get("name") or f"Produto {woo_id}",
        "slug": woo_product.get("slug"),
        "short_description": _strip_html(woo_product.get("short_description")),
        "description": _strip_html(woo_product.get("description")),
        "price": _to_float(woo_product.get("price")) or 0.0,
        "currency": "BRL",
        "weight_g": _to_float(woo_product.get("weight")),
        "package_length_cm": _to_float(woo_product.get("dimensions", {}).get("length")),
        "package_width_cm": _to_float(woo_product.get("dimensions", {}).get("width")),
        "package_height_cm": _to_float(woo_product.get("dimensions", {}).get("height")),
        "composition": composition,
        "categories": _parse_categories(woo_product.get("categories")),
        "tags": _parse_categories(woo_product.get("tags")),
        "active": woo_product.get("status") == "publish",
    }

    if product is None:
        product = Product(**payload)
        session.add(product)
    else:
        for key, value in payload.items():
            setattr(product, key, value)
        session.add(product)

    session.flush()

    existing_images = session.exec(select(ProductImage).where(ProductImage.product_id == product.id)).all()
    for image in existing_images:
        session.delete(image)

    images = _parse_images(woo_product.get("images"))
    for order, image_url in enumerate(images):
        session.add(
            ProductImage(
                product_id=product.id,
                source_url=image_url,
                file_name=image_url.rsplit("/", 1)[-1],
                sort_order=order,
            )
        )

    return 1, len(images)


async def sync_products_from_woocommerce(session: Session) -> tuple[int, int]:
    settings = get_settings()
    if not settings.woocommerce_base_url or not settings.woocommerce_consumer_key or not settings.woocommerce_consumer_secret:
        raise ValueError("Configuracao do WooCommerce incompleta.")

    base_url = settings.woocommerce_base_url.rstrip("/")
    endpoint = f"{base_url}/wp-json/wc/v3/products"

    imported_products = 0
    imported_images = 0

    async with httpx.AsyncClient(timeout=40.0) as client:
        page = 1
        while True:
            response = await client.get(
                endpoint,
                params={
                    "consumer_key": settings.woocommerce_consumer_key,
                    "consumer_secret": settings.woocommerce_consumer_secret,
                    "per_page": 100,
                    "page": page,
                    "status": "any",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload:
                break

            for product in payload:
                count_products, count_images = _upsert_product_from_woo(session, product)
                imported_products += count_products
                imported_images += count_images

            session.commit()
            page += 1

    return imported_products, imported_images