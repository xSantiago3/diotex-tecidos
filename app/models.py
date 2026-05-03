from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


class OrderStatus(str, Enum):
    draft = "draft"
    awaiting_payment = "awaiting_payment"
    payment_under_review = "payment_under_review"
    paid = "paid"
    preparing = "preparing"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    whatsapp_phone: str = Field(index=True, unique=True)
    name: str | None = None
    email: str | None = None
    zipcode: str | None = Field(default=None, index=True)
    preferred_language: str = Field(default="pt-BR")
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    woo_product_id: int | None = Field(default=None, index=True, unique=True)
    product_type: str | None = None
    name: str = Field(index=True)
    slug: str | None = Field(default=None, index=True)
    short_description: str | None = None
    description: str | None = None
    price: float = Field(default=0.0)
    currency: str = Field(default="BRL")
    weight_g: float | None = None
    package_length_cm: float | None = None
    package_width_cm: float | None = None
    package_height_cm: float | None = None
    width_cm: float | None = None
    composition: str | None = None
    color: str | None = None
    categories: str | None = None
    tags: str | None = None
    unit_type: str = Field(default="metro")
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class ProductImage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(index=True, foreign_key="product.id")
    source_url: str
    file_name: str | None = None
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Inventory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(index=True, foreign_key="product.id", unique=True)
    available_quantity: float = Field(default=0.0)
    reserved_quantity: float = Field(default=0.0)
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(index=True, foreign_key="customer.id")
    channel: str = Field(default="whatsapp")
    shipping_zipcode: str | None = None
    shipping_quote_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    subtotal_amount: float = Field(default=0.0)
    shipping_amount: float = Field(default=0.0)
    total_amount: float = Field(default=0.0)
    payment_provider: str | None = None
    payment_reference: str | None = Field(default=None, index=True)
    status: OrderStatus = Field(default=OrderStatus.draft)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class OrderItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    order_id: int = Field(index=True, foreign_key="order.id")
    product_id: int | None = Field(default=None, foreign_key="product.id")
    product_name_snapshot: str
    unit_price_snapshot: float
    quantity: float
    line_total: float


class AdminAction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    actor_phone: str = Field(index=True)
    action_type: str
    target_table: str
    target_id: str
    payload_before_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    payload_after_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    otp_verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class OtpChallenge(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    actor_phone: str = Field(index=True)
    purpose: str
    secret: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)