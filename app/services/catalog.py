from __future__ import annotations

from sqlmodel import Session, select

from app.models import Inventory, Product, ProductImage
from app.schemas import CatalogListResponse, CatalogProductResponse


def list_catalog_products(session: Session, search: str | None = None, limit: int = 50, offset: int = 0) -> CatalogListResponse:
    statement = select(Product).where(Product.active.is_(True)).order_by(Product.name).offset(offset).limit(limit)
    if search:
        statement = statement.where(Product.name.ilike(f"%{search}%"))

    products = session.exec(statement).all()
    items: list[CatalogProductResponse] = []

    for product in products:
        inventory = session.exec(select(Inventory).where(Inventory.product_id == product.id)).first()
        image_rows = session.exec(
            select(ProductImage)
            .where(ProductImage.product_id == product.id)
            .order_by(ProductImage.sort_order.asc())
        ).all()
        available_quantity = 0.0
        if inventory:
            available_quantity = max(inventory.available_quantity - inventory.reserved_quantity, 0.0)

        items.append(
            CatalogProductResponse(
                product_id=product.id or 0,
                woo_product_id=product.woo_product_id,
                name=product.name,
                price=product.price,
                currency=product.currency,
                available_quantity=available_quantity,
                unit_type=product.unit_type,
                categories=product.categories,
                tags=product.tags,
                image_urls=[image.source_url for image in image_rows if image.source_url],
            )
        )

    return CatalogListResponse(items=items, total=len(items))