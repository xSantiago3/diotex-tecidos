from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Product, ProductImage


FIELD_ALIASES = {
    "woo_product_id": ["woo_product_id", "woocommerce_id", "product_id", "id"],
    "product_type": ["tipo", "type", "product_type"],
    "name": ["name", "nome", "produto"],
    "slug": ["slug"],
    "short_description": ["descricao curta", "descrição curta", "short_description"],
    "description": ["description", "descricao"],
    "price": ["price", "preco", "valor"],
    "currency": ["currency", "moeda"],
    "weight_g": ["peso (g)", "peso_g", "weight_g", "weight"],
    "package_length_cm": ["comprimento (cm)", "package_length_cm"],
    "package_width_cm": ["largura (cm)", "package_width_cm"],
    "package_height_cm": ["altura (cm)", "package_height_cm"],
    "width_cm": ["width_cm", "largura_cm", "largura"],
    "composition": ["composition", "composicao"],
    "color": ["color", "cor"],
    "categories": ["categorias", "categories"],
    "tags": ["tags"],
    "images": ["imagens", "images", "image"],
    "unit_type": ["unit_type", "unidade", "tipo_unidade"],
    "active": ["active", "ativo"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa produtos para a base local.")
    parser.add_argument("file", type=Path, help="Arquivo CSV ou JSON com os produtos")
    return parser.parse_args()


def read_rows(file_path: Path) -> list[dict[str, Any]]:
    if file_path.suffix.lower() == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [dict(item) for item in data]
        raise ValueError("O JSON deve conter uma lista de objetos.")

    if file_path.suffix.lower() == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as file_handle:
            return list(csv.DictReader(file_handle))

    raise ValueError("Formato nao suportado. Use CSV ou JSON.")


def normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    no_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return no_accents.strip().lower()


def first_value(row: dict[str, Any], aliases: list[str]) -> Any:
    lowered = {normalize_key(str(key)): value for key, value in row.items()}
    for alias in aliases:
        normalized_alias = normalize_key(alias)
        if normalized_alias in lowered:
            return lowered[normalized_alias]
    return None


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in raw and "." in raw:
        normalized = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        normalized = raw.replace(",", ".")
    else:
        normalized = raw
    return float(normalized)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() in {"1", "true", "sim", "yes", "y", "ativo"}


def parse_fabric_width_cm_from_name(name: str) -> float | None:
    match = re.search(r"\(([^)]*)\)", name)
    if not match:
        return None

    inside = match.group(1).lower().replace(" ", "")
    parts = re.split(r"x", inside)
    if len(parts) < 2:
        return None

    raw_width = parts[1].replace("m", "").replace(",", ".")
    try:
        width_m = float(raw_width)
    except ValueError:
        return None
    return round(width_m * 100.0, 2)


def split_images(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    parts = [piece.strip() for piece in str(value).split(",")]
    return [part for part in parts if part]


def map_row(row: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for target_field, aliases in FIELD_ALIASES.items():
        mapped[target_field] = first_value(row, aliases)

    if not mapped["name"]:
        raise ValueError(f"Linha sem nome de produto: {row}")

    name = str(mapped["name"]).strip()
    fabric_width_cm = parse_fabric_width_cm_from_name(name)

    payload = {
        "woo_product_id": int(mapped["woo_product_id"]) if mapped["woo_product_id"] not in (None, "") else None,
        "product_type": str(mapped["product_type"]).strip() if mapped["product_type"] not in (None, "") else None,
        "name": name,
        "slug": str(mapped["slug"]).strip() if mapped["slug"] not in (None, "") else None,
        "short_description": str(mapped["short_description"]).strip() if mapped["short_description"] not in (None, "") else None,
        "description": str(mapped["description"]).strip() if mapped["description"] not in (None, "") else None,
        "price": parse_float(mapped["price"]) or 0.0,
        "currency": str(mapped["currency"]).strip() if mapped["currency"] not in (None, "") else "BRL",
        "weight_g": parse_float(mapped["weight_g"]),
        "package_length_cm": parse_float(mapped["package_length_cm"]),
        "package_width_cm": parse_float(mapped["package_width_cm"]),
        "package_height_cm": parse_float(mapped["package_height_cm"]),
        "width_cm": fabric_width_cm,
        "composition": str(mapped["composition"]).strip() if mapped["composition"] not in (None, "") else None,
        "color": str(mapped["color"]).strip() if mapped["color"] not in (None, "") else None,
        "categories": str(mapped["categories"]).strip() if mapped["categories"] not in (None, "") else None,
        "tags": str(mapped["tags"]).strip() if mapped["tags"] not in (None, "") else None,
        "unit_type": str(mapped["unit_type"]).strip() if mapped["unit_type"] not in (None, "") else "metro",
        "active": parse_bool(mapped["active"]),
        "images": split_images(mapped["images"]),
    }
    return payload


def upsert_product(session: Session, payload: dict[str, Any]) -> Product:
    image_urls = payload.pop("images", [])
    product: Product | None = None
    if payload["woo_product_id"] is not None:
        product = session.exec(select(Product).where(Product.woo_product_id == payload["woo_product_id"])).first()
    if product is None:
        product = session.exec(select(Product).where(Product.name == payload["name"])).first()

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
    for order, image_url in enumerate(image_urls):
        session.add(
            ProductImage(
                product_id=product.id,
                source_url=image_url,
                file_name=image_url.rsplit("/", 1)[-1],
                sort_order=order,
            )
        )

    return product


def main() -> None:
    args = parse_args()
    rows = read_rows(args.file)
    mapped_rows = [map_row(row) for row in rows]

    init_db()
    with Session(engine) as session:
        for row in mapped_rows:
            upsert_product(session, row)
        session.commit()

    print(f"imported-products={len(mapped_rows)}")


if __name__ == "__main__":
    main()