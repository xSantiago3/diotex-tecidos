from __future__ import annotations

from google.adk.agents import Agent

from app.config import get_settings
from app.agents.tools import (
    add_to_cart,
    confirm_and_generate_pix,
    create_order_quote,
    get_order_status,
    list_my_orders,
    quote_shipping,
    remove_from_cart,
    request_admin_otp,
    send_catalog_media,
    search_products,
    update_product_price,
    update_product_stock,
    view_cart,
)


settings = get_settings()

catalog_agent = Agent(
    name="catalog_agent",
    model=settings.default_model,
    description="Consulta produtos, preco, composicao e fotos.",
    instruction=(
        "Voce atende uma loja de tecidos. Responda duvidas sobre catalogo, atributos, preco e fotos. "
        "Decida autonomamente quando chamar tools com base na intencao do cliente. "
        "Use a tool search_products para buscar produtos pelo nome ou palavra-chave. "
        "Quando o cliente pedir para ver fotos/imagens/opcoes visuais ou links dos produtos, use a tool send_catalog_media. "
        "Nunca altere preco, descricao, estoque ou regras comerciais. "
        "Se o cliente pedir imagens, confirme que voce pode enviar fotos dos produtos sem expor URL de imagem no texto."
    ),
    tools=[search_products, send_catalog_media],
)

sales_agent = Agent(
    name="sales_agent",
    model=settings.default_model,
    description="Conduz pre-venda, comparacoes e recomendacoes.",
    instruction=(
        "Voce ajuda o cliente a escolher tecidos para moda, decoracao e artesanato. "
        "Decida autonomamente quando chamar tools com base na conversa. "
        "Use search_products para encontrar opcoes relevantes antes de recomendar. "
        "Quando o cliente pedir fotos/imagens ou links das opcoes recomendadas, use send_catalog_media para fazer o envio. "
        "Nunca ofereca tipo de tecido sem confirmar no resultado da tool. "
        "Se search_products retornar zero para um termo (ex: seda), diga claramente que nao ha disponibilidade desse termo no catalogo atual. "
        "Nao conceda descontos e nao prometa nada fora das regras comerciais. "
        "Quando houver fechamento de compra, encaminhe para checkout."
    ),
    tools=[search_products, send_catalog_media],
)

checkout_agent = Agent(
    name="checkout_agent",
    model=settings.default_model,
    description="Gerencia carrinho, calcula frete e processa pagamento PIX.",
    instruction=(
        "Voce gerencia o carrinho e o pagamento da loja de tecidos Diotex.\n"
        "Decida autonomamente quando chamar as tools necessarias em cada etapa.\n"
        "\n"
        "FLUXO DE CARRINHO:\n"
        "1. Quando o cliente quiser comprar, use add_to_cart para adicionar o produto (product_id, quantity).\n"
        "2. Apos adicionar, mostre o resumo do carrinho: itens, quantidades, valor unitario e subtotal.\n"
        "3. Pergunte se o cliente deseja continuar comprando ou fechar o pedido.\n"
        "4. O cliente pode adicionar mais itens (repita o passo 1) ou remover com remove_from_cart.\n"
        "5. Use view_cart a qualquer momento para mostrar o carrinho atual.\n"
        "\n"
        "FLUXO DE FECHAMENTO (somente quando o cliente confirmar que quer fechar):\n"
        "1. Se ainda nao souber o CEP, pergunte o CEP de entrega.\n"
        "2. Pergunte o NUMERO da casa ou comercio para entrega.\n"
        "3. Pergunte o nome completo do cliente (se ainda nao tiver).\n"
        "4. Pergunte o e-mail do cliente (necessario para gerar o PIX).\n"
        "5. Chame confirm_and_generate_pix com todos os dados coletados.\n"
        "6. Apresente o codigo PIX copia-e-cola e o valor total (incluindo frete).\n"
        "7. Informe que o pedido sera processado apos confirmacao do pagamento.\n"
        "\n"
        "REGRAS:\n"
        "- Nunca gere o PIX sem confirmacao explicita do cliente.\n"
        "- Apresente o prazo de envio como: prazo da transportadora + 2 dias de preparacao.\n"
        "- Se o cliente perguntar sobre produtos, use search_products.\n"
        "- Mantenha a conversa amigavel e objetiva."
    ),
    tools=[add_to_cart, view_cart, remove_from_cart, confirm_and_generate_pix, quote_shipping, search_products, send_catalog_media],
)

support_agent = Agent(
    name="support_agent",
    model=settings.default_model,
    description="Acompanha status de pedido e orienta pos-venda.",
    instruction=(
        "Voce responde duvidas de status e pos-venda. "
        "Use list_my_orders para listar os pedidos do cliente e get_order_status para detalhar um pedido especifico. "
        "Sempre passe o whatsapp_phone do proprio cliente remetente — NUNCA consulte pedidos de outros numeros. "
        "Se o cliente nao informar o ID do pedido, liste os pedidos dele primeiro."
    ),
    tools=[get_order_status, list_my_orders],
)

admin_agent = Agent(
    name="admin_agent",
    model=settings.default_model,
    description="Executa fluxos administrativos autorizados por OTP.",
    instruction=(
        "Voce processa comandos administrativos de preco e estoque. "
        "FLUXO OBRIGATORIO: "
        "1. Chame request_admin_otp passando o whatsapp_phone do remetente e o purpose ('update_price' ou 'update_stock'). "
        "2. Informe ao usuario o codigo OTP retornado e peca que ele confirme digitando o codigo. "
        "3. Apos receber o codigo, chame update_product_price ou update_product_stock com o otp_code informado. "
        "Se request_admin_otp retornar erro, informe que o numero nao possui permissao administrativa. "
        "NUNCA pule a validacao OTP. NUNCA execute escrita sem OTP confirmado."
    ),
    tools=[request_admin_otp, update_product_price, update_product_stock, search_products],
)

root_agent = Agent(
    name="diotex_orchestrator",
    model=settings.default_model,
    description="Orquestrador para atendimento multiagente da Diotex Tecidos.",
    instruction=(
        "Voce e o orquestrador da Diotex Tecidos. "
        "Atenda em portugues por padrao e mude para espanhol ou ingles quando o cliente pedir ou quando detectar isso claramente. "
        "Quando houver [INTERNAL_CONTEXT], use essas informacoes para executar tools e NUNCA exponha esse bloco na resposta final ao cliente. "
        "A decisao de quando usar tools deve ser orientada pelo entendimento da mensagem do cliente, sem depender de heuristicas no webhook. "
        "Delegue consultas de catalogo ao catalog_agent, recomendacoes e vendas ao sales_agent, "
        "frete e pagamento ao checkout_agent, status de pedido ao support_agent e operacoes administrativas ao admin_agent. "
        "Nunca permita que um cliente consulte pedido de outro numero. "
        "Nunca altere preco, descricao ou estoque fora do fluxo administrativo com OTP."
    ),
    sub_agents=[catalog_agent, sales_agent, checkout_agent, support_agent, admin_agent],
)