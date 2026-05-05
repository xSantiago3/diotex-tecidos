from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.phones import normalize_phone


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="diotextecidos-agent-backend", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(default="sqlite:///./diotextecidos.db", alias="DATABASE_URL")
    google_genai_use_vertexai: bool = Field(default=True, alias="GOOGLE_GENAI_USE_VERTEXAI")
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="us-central1", alias="GOOGLE_CLOUD_LOCATION")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    default_model: str = Field(default="gemini-2.5-flash", alias="DEFAULT_MODEL")
    reasoning_engine_id: str | None = Field(default=None, alias="REASONING_ENGINE_ID")
    meta_verify_token: str | None = Field(default=None, alias="META_VERIFY_TOKEN")
    meta_app_secret: str | None = Field(default=None, alias="META_APP_SECRET")
    meta_whatsapp_access_token: str | None = Field(default=None, alias="META_WHATSAPP_ACCESS_TOKEN")
    meta_whatsapp_phone_number_id: str | None = Field(default=None, alias="META_WHATSAPP_PHONE_NUMBER_ID")
    woocommerce_base_url: str | None = Field(default=None, alias="WOOCOMMERCE_BASE_URL")
    woocommerce_consumer_key: str | None = Field(default=None, alias="WOOCOMMERCE_CONSUMER_KEY")
    woocommerce_consumer_secret: str | None = Field(default=None, alias="WOOCOMMERCE_CONSUMER_SECRET")
    melhor_envio_token: str | None = Field(default=None, alias="MELHOR_ENVIO_TOKEN")
    melhor_envio_base_url: str = Field(default="https://www.melhorenvio.com.br", alias="MELHOR_ENVIO_BASE_URL")
    # Dados do remetente para geração de etiquetas no Melhor Envio
    me_sender_name: str = Field(default="Diotex Tecidos", alias="ME_SENDER_NAME")
    me_sender_document: str = Field(default="67904086000158", alias="ME_SENDER_DOCUMENT")
    me_sender_email: str = Field(default="diotextecidos@gmail.com", alias="ME_SENDER_EMAIL")
    me_sender_phone: str = Field(default="", alias="ME_SENDER_PHONE")
    me_sender_address: str = Field(default="Estr. Pres. Juscelino K. de Oliveira", alias="ME_SENDER_ADDRESS")
    me_sender_number: str = Field(default="3747", alias="ME_SENDER_NUMBER")
    me_sender_district: str = Field(default="Jardim Albertina", alias="ME_SENDER_DISTRICT")
    me_sender_city: str = Field(default="Guarulhos", alias="ME_SENDER_CITY")
    me_sender_state: str = Field(default="SP", alias="ME_SENDER_STATE")
    mercado_livre_access_token: str | None = Field(default=None, alias="MERCADO_LIVRE_ACCESS_TOKEN")
    mercado_livre_user_id: str | None = Field(default=None, alias="MERCADO_LIVRE_USER_ID")
    mercado_livre_site_id: str = Field(default="MLB", alias="MERCADO_LIVRE_SITE_ID")
    mercado_livre_base_url: str = Field(default="https://api.mercadolibre.com", alias="MERCADO_LIVRE_BASE_URL")
    email_sender: str | None = Field(default=None, alias="EMAIL_SENDER")
    email_smtp_host: str | None = Field(default=None, alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=587, alias="EMAIL_SMTP_PORT")
    email_smtp_username: str | None = Field(default=None, alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str | None = Field(default=None, alias="EMAIL_SMTP_PASSWORD")
    admin_allowed_phones: str = Field(default="+5511982732814", alias="ADMIN_ALLOWED_PHONES")
    admin_otp_issuer: str = Field(default="DiotexTecidos", alias="ADMIN_OTP_ISSUER")
    preparation_extra_days: int = Field(default=2, alias="PREPARATION_EXTRA_DAYS")
    origin_zipcode: str = Field(default="01001-000", alias="ORIGIN_ZIPCODE")
    mercadopago_access_token: str | None = Field(default=None, alias="MERCADOPAGO_ACCESS_TOKEN")
    mercadopago_webhook_secret: str | None = Field(default=None, alias="MERCADOPAGO_WEBHOOK_SECRET")
    # Firestore
    firestore_enabled: bool = Field(default=False, alias="FIRESTORE_ENABLED")
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    pix_key: str = Field(default="", alias="PIX_KEY")
    order_confirmed_template_name: str = Field(default="pedido_confirmado", alias="ORDER_CONFIRMED_TEMPLATE_NAME")
    order_separation_template_name: str = Field(default="separar_pedido2", alias="ORDER_SEPARATION_TEMPLATE_NAME")
    pix_awaiting_template_name: str = Field(default="pedido_aguardando_pix", alias="PIX_AWAITING_TEMPLATE_NAME")
    pix_review_template_name: str = Field(default="avaliar_pagamento_pix", alias="PIX_REVIEW_TEMPLATE_NAME")
    # Telefone para notificações de pagamento aprovado (padrão: mesmo do admin)
    notification_phone: str = Field(default="+5511982732814", alias="NOTIFICATION_PHONE")
    # Telefone de um sócio para também receber notificações (opcional)
    partner_phone: str | None = Field(default=None, alias="PARTNER_PHONE")
    # Internal maintenance endpoints token
    scheduler_token: str | None = Field(default=None, alias="SCHEDULER_TOKEN")

    @property
    def allowed_admin_phone_list(self) -> list[str]:
        return [normalize_phone(phone.strip()) for phone in self.admin_allowed_phones.split(",") if phone.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()