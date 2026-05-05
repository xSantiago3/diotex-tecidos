#!/usr/bin/env python3
"""Apaga TODOS os dados de um número no Firestore (pedidos, carrinhos, cadastro).

Uso:
    python scripts/delete_customer.py 5511982732814
    python scripts/delete_customer.py 5511982732814 --dry-run

Requer GOOGLE_CLOUD_PROJECT no ambiente ou via .env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Adiciona o root do projeto ao path para importar app.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.firestore_db import delete_customer_data_firestore, get_customer_firestore, init_firestore


async def main(phone: str, dry_run: bool) -> None:
    init_firestore()

    customer = await get_customer_firestore(phone)
    if not customer:
        print(f"[INFO] Nenhum cliente encontrado para {phone} no Firestore.")
    else:
        print(f"[INFO] Cliente encontrado: {customer.get('name') or '(sem nome)'} — {phone}")

    if dry_run:
        print("[DRY-RUN] Nenhum dado foi apagado.")
        return

    confirm = input(f"\nTem certeza que quer apagar TODOS os dados de {phone}? [s/N] ").strip().lower()
    if confirm != "s":
        print("Cancelado.")
        return

    result = await delete_customer_data_firestore(phone)
    print("\n[OK] Dados apagados:")
    for key, count in result.items():
        print(f"  {key}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apaga dados de um cliente no Firestore.")
    parser.add_argument("phone", help="Número de WhatsApp normalizado (ex: 5511982732814)")
    parser.add_argument("--dry-run", action="store_true", help="Apenas mostra o cliente sem apagar")
    args = parser.parse_args()

    asyncio.run(main(args.phone, args.dry_run))
