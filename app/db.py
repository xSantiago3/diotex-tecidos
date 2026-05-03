from collections.abc import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)
_db_initialized = False


def _ensure_sqlite_schema_updates() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as connection:
        product_table_info = connection.execute(text("PRAGMA table_info(product)")).fetchall()
        customer_table_info = connection.execute(text("PRAGMA table_info(customer)")).fetchall()
        if not product_table_info:
            return

        existing_product_columns = {row[1] for row in product_table_info}
        product_column_updates = {
            "product_type": "TEXT",
            "short_description": "TEXT",
            "weight_g": "REAL",
            "package_length_cm": "REAL",
            "package_width_cm": "REAL",
            "package_height_cm": "REAL",
            "categories": "TEXT",
            "tags": "TEXT",
        }

        for column_name, sql_type in product_column_updates.items():
            if column_name not in existing_product_columns:
                connection.execute(text(f"ALTER TABLE product ADD COLUMN {column_name} {sql_type}"))

        if customer_table_info:
            existing_customer_columns = {row[1] for row in customer_table_info}
            if "zipcode" not in existing_customer_columns:
                connection.execute(text("ALTER TABLE customer ADD COLUMN zipcode TEXT"))


def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    SQLModel.metadata.create_all(engine)
    _ensure_sqlite_schema_updates()
    _db_initialized = True


def get_session() -> Iterator[Session]:
    init_db()
    with Session(engine) as session:
        yield session