"""Envia os 4 templates WhatsApp com dados fictícios para um número de teste."""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from app.services.whatsapp import send_whatsapp_template

TO_PHONE = "5511982732814"

TEMPLATES = [
    {
        "name": "pedido_aguardando_pix",
        "description": "Cliente aguardando pagamento via PIX",
        "variables": [
            "Santiago",       # {{1}} primeiro nome
            "999",            # {{2}} número do pedido
            "Helanca Verde (2m), Malha Branca (1m)",  # {{3}} produtos
            "150,00",         # {{4}} valor total
            "11.999.999/0001-00",  # {{5}} chave PIX
        ],
    },
    {
        "name": "pedido_confirmado",
        "description": "Pagamento confirmado para o cliente",
        "variables": [
            "Santiago",       # {{1}} primeiro nome
            "999",            # {{2}} número do pedido
            "Helanca Verde (2m), Malha Branca (1m)",  # {{3}} produtos
            "150,00",         # {{4}} valor total
            "5 dias úteis",   # {{5}} prazo de entrega
        ],
    },
    {
        "name": "avaliar_pagamento_pix",
        "description": "Admin avalia comprovante PIX (com botões)",
        "variables": [
            "999",            # {{1}} número do pedido
            "Santiago Teste", # {{2}} nome do cliente
            "5511982732814",  # {{3}} telefone do cliente
            "Helanca Verde (2m), Malha Branca (1m)",  # {{4}} produtos
            "150,00",         # {{5}} valor total
        ],
    },
    {
        "name": "separar_pedido2",
        "description": "Admin separa pedido para envio",
        "variables": [
            "999",            # {{1}} número do pedido
            "Santiago Teste", # {{2}} nome do cliente
            "Helanca Verde (2m), Malha Branca (1m)",  # {{3}} produtos
            "150,00",         # {{4}} valor total
            "01310-100, nº 45",  # {{5}} endereço (CEP + número)
            "https://etiqueta.exemplo.com/999.pdf",  # {{6}} link etiqueta
            "BR123456789BR",  # {{7}} código de rastreio
        ],
    },
]


async def main() -> None:
    for tpl in TEMPLATES:
        print(f"\n--- {tpl['name']} ---")
        print(f"    {tpl['description']}")
        result = await send_whatsapp_template(
            to_phone=TO_PHONE,
            template_name=tpl["name"],
            body_variables=tpl["variables"],
        )
        if isinstance(result, dict) and result.get("error"):
            print(f"    ERRO: {result}")
        else:
            msg_id = (result.get("messages") or [{}])[0].get("id", "?")
            print(f"    OK — message_id: {msg_id}")


if __name__ == "__main__":
    asyncio.run(main())
