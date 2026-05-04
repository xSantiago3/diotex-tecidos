from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models import OrderStatus


class HealthResponse(BaseModel):
    status: str
    app_name: str


class CustomerUpsert(BaseModel):
    whatsapp_phone: str
    name: str | None = None
    email: str | None = None
    zipcode: str | None = None
    preferred_language: str | None = None


class OrderItemSummary(BaseModel):
    product_name: str
    quantity: float
    unit_price: float
    line_total: float


class OrderStatusResponse(BaseModel):
    order_id: int
    customer_whatsapp_phone: str
    status: OrderStatus
    subtotal_amount: float
    shipping_amount: float
    total_amount: float
    channel: str
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemSummary]
    shipping_quote: dict[str, Any] | None = None


class AdminOtpStartRequest(BaseModel):
    whatsapp_phone: str
    purpose: str = Field(default="admin_update")


class AdminOtpVerifyRequest(BaseModel):
    whatsapp_phone: str
    purpose: str = Field(default="admin_update")
    otp_code: str


class AdminOtpResponse(BaseModel):
    message: str
    expires_at: datetime | None = None


class WhatsAppWebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    object: str | None = None
    entry: list[dict[str, Any]] = []


class CatalogProductResponse(BaseModel):
    product_id: int
    woo_product_id: int | None
    name: str
    price: float
    currency: str
    available_quantity: float
    unit_type: str
    categories: str | None = None
    tags: str | None = None
    product_url: str | None = None
    image_urls: list[str] = []


class CatalogListResponse(BaseModel):
    items: list[CatalogProductResponse]
    total: int


class ShippingProvider(str, Enum):
    melhor_envio = "melhor_envio"
    mercado_livre = "mercado_livre"


class ShippingQuoteRequest(BaseModel):
    provider: ShippingProvider
    to_zipcode: str
    product_name: str
    quantity: int = Field(default=1, ge=1)
    unit_price: float = Field(default=0.0, ge=0)
    weight_g: float = Field(gt=0)
    package_length_cm: float = Field(gt=0)
    package_width_cm: float = Field(gt=0)
    package_height_cm: float = Field(gt=0)
    insurance_value: float | None = Field(default=None, ge=0)
    mercado_livre_item_id: str | None = None


class ShippingQuoteOption(BaseModel):
    provider: ShippingProvider
    carrier_name: str | None = None
    service_name: str
    service_code: str | None = None
    price: float
    currency: str = "BRL"
    delivery_days: int | None = None
    delivery_days_with_preparation: int | None = None
    raw: dict[str, Any] = {}


class ShippingQuoteResponse(BaseModel):
    options: list[ShippingQuoteOption]


class WooSyncResponse(BaseModel):
    imported_products: int
    imported_images: int


class AgentChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str


class AgentChatResponse(BaseModel):
    response: str


class CheckoutItemRequest(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class CheckoutQuoteRequest(BaseModel):
    whatsapp_phone: str
    items: list[CheckoutItemRequest]
    zipcode: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    channel: str = "whatsapp"


class CheckoutItemResponse(BaseModel):
    product_id: int
    name: str
    quantity: float
    unit_price: float
    line_total: float


class CheckoutQuoteResponse(BaseModel):
    requires_zipcode: bool = False
    message: str | None = None
    customer_whatsapp_phone: str
    zipcode_used: str | None = None
    order_id: int | None = None
    provider: ShippingProvider | None = None
    carrier_name: str | None = None
    service_name: str | None = None
    shipping_amount: float = 0.0
    subtotal_amount: float = 0.0
    total_amount: float = 0.0
    delivery_days_with_preparation: int | None = None
    items: list[CheckoutItemResponse] = []