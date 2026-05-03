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
    meta_verify_token: str | None = Field(default=None, alias="META_VERIFY_TOKEN")
    meta_app_secret: str | None = Field(default=None, alias="META_APP_SECRET")
    meta_whatsapp_access_token: str | None = Field(default=None, alias="META_WHATSAPP_ACCESS_TOKEN")
    meta_whatsapp_phone_number_id: str | None = Field(default=None, alias="META_WHATSAPP_PHONE_NUMBER_ID")
    woocommerce_base_url: str | None = Field(default=None, alias="WOOCOMMERCE_BASE_URL")
    woocommerce_consumer_key: str | None = Field(default=None, alias="WOOCOMMERCE_CONSUMER_KEY")
    woocommerce_consumer_secret: str | None = Field(default=None, alias="WOOCOMMERCE_CONSUMER_SECRET")
    melhor_envio_token: str | None = Field(default=None, alias="MELHOR_ENVIO_TOKEN")
    melhor_envio_base_url: str = Field(default="https://www.melhorenvio.com.br", alias="MELHOR_ENVIO_BASE_URL")
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

    @property
    def allowed_admin_phone_list(self) -> list[str]:
        return [normalize_phone(phone.strip()) for phone in self.admin_allowed_phones.split(",") if phone.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()