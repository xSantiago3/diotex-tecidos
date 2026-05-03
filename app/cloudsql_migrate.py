from __future__ import annotations

import argparse

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AdminAction, Customer, Inventory, Order, OrderItem, OtpChallenge, Product, ProductImage


TABLE_ORDER = [Customer, Product, ProductImage, Inventory, Order, OrderItem, AdminAction, OtpChallenge]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migra dados do SQLite local para PostgreSQL/Cloud SQL.")
    parser.add_argument("--source-url", required=True, help="Ex.: sqlite:///./diotextecidos.db")
    parser.add_argument("--target-url", required=True, help="Ex.: postgresql+psycopg://user:pass@host/db")
    return parser.parse_args()


def migrate(source_url: str, target_url: str) -> None:
    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    SQLModel.metadata.create_all(target_engine)

    with Session(source_engine) as source_session, Session(target_engine) as target_session:
        for model in TABLE_ORDER:
            rows = source_session.exec(select(model)).all()
            for row in rows:
                target_session.merge(model(**row.model_dump()))
            target_session.commit()
            print(f"migrated_{model.__name__.lower()}={len(rows)}")


def main() -> None:
    args = parse_args()
    migrate(args.source_url, args.target_url)


if __name__ == "__main__":
    main()
