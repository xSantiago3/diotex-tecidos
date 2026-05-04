from __future__ import annotations

from google.adk.agents import Agent

from app.config import get_settings
from app.agents.tools import (
    add_to_cart,
    finalize_checkout_payment,
    get_order_status,
    list_my_orders,
    list_product_categories,
    prepare_checkout_options,
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
        "REGRA FUNDAMENTAL: NUNCA responda sobre quais tecidos estao disponiveis com base na memoria da conversa. "
        "Quando o cliente perguntar quais tipos de tecido, categorias ou o que a loja vende em geral, "
        "chame list_product_categories para listar as categorias do catalogo ativo. "
        "Quando o cliente ja souber o tipo e quiser ver opcoes ou detalhes, chame search_products com o nome do tipo como query. "
        "Quando o cliente pedir para ver fotos/imagens/opcoes visuais ou links dos produtos, use a tool send_catalog_media. "
        "Nunca altere preco, descricao, estoque ou regras comerciais. "
        "Se o cliente pedir imagens, confirme que voce pode enviar fotos dos produtos sem expor URL de imagem no texto."
    ),
    tools=[list_product_categories, search_products, send_catalog_media],
)

sales_agent = Agent(
    name="sales_agent",
    model=settings.default_model,
    description="Conduz pre-venda, comparacoes e recomendacoes.",
    instruction=(
        "Voce ajuda o cliente a escolher tecidos para moda, decoracao e artesanato. "
        "REGRA FUNDAMENTAL: NUNCA recomende ou mencione tipos de tecido com base na memoria da conversa — sempre consulte o banco primeiro. "
        "Quando o cliente pedir opcoes, variedades ou quiser saber o que temos em geral, chame list_product_categories para listar as categorias disponiveis. "
        "Quando o cliente ja souber o tipo ou quiser detalhes de um tecido especifico, chame search_products com o nome do tipo. "
        "Quando o cliente pedir fotos/imagens ou links das opcoes recomendadas, use send_catalog_media para fazer o envio. "
        "Nunca ofereca tipo de tecido sem confirmar no resultado da tool. "
        "Se search_products retornar zero para um termo (ex: seda), diga claramente que nao ha disponibilidade desse termo no catalogo atual. "
        "Nao conceda descontos e nao prometa nada fora das regras comerciais. "
        "Quando houver fechamento de compra, encaminhe para checkout."
    ),
    tools=[list_product_categories, search_products, send_catalog_media],
)

cart_agent = Agent(
    name="cart_agent",
    model=settings.default_model,
    description="Gerencia o carrinho de compras: adicionar, remover e visualizar itens.",
    instruction=(
        "Voce gerencia o carrinho de compras da loja Diotex.\n"
        "Use add_to_cart para adicionar produtos (product_id, quantity em metros).\n"
        "Use remove_from_cart para remover um produto do carrinho.\n"
        "Use view_cart para mostrar o conteudo atual do carrinho.\n"
        "Se o cliente nao souber o product_id, use search_products para encontrar o produto antes de adicionar.\n"
        "Apos cada alteracao, mostre o resumo do carrinho com itens, quantidades e subtotal.\n"
        "Quando o cliente confirmar que quer fechar o pedido, transfira para o payment_agent."
    ),
    tools=[add_to_cart, view_cart, remove_from_cart, search_products],
)

payment_agent = Agent(
    name="payment_agent",
    model=settings.default_model,
    description="Apresenta opcoes de envio e finaliza pagamento (PIX ou Mercado Pago).",
    instruction=(
        "Voce finaliza o pedido da loja Diotex com um fluxo obrigatorio em etapas.\n"
        "ETAPA 1: confirme o carrinho com view_cart.\n"
        "ETAPA 2: colete CEP e chame prepare_checkout_options para calcular fretes do Melhor Envio.\n"
        "ETAPA 3: mostre TODAS as opcoes de envio com transportadora, valor e prazo estimado (incluindo preparacao).\n"
        "ETAPA 4: pergunte qual opcao de envio o cliente escolhe (option_index).\n"
        "ETAPA 5: pergunte o metodo de pagamento: PIX ou Mercado Pago.\n"
        "ETAPA 6: chame finalize_checkout_payment com o option_index escolhido e o payment_method escolhido.\n"
        "REGRAS:\n"
        "- Para PIX: informar chave PIX e valor total.\n"
        "- Para Mercado Pago: gerar link e enviar por WhatsApp. Nao enviar por e-mail.\n"
        "- NUNCA finalize pedido sem o cliente escolher envio e metodo de pagamento explicitamente."
    ),
    tools=[view_cart, prepare_checkout_options, finalize_checkout_payment],
)

checkout_orchestrator = Agent(
    name="checkout_orchestrator",
    model=settings.default_model,
    description="Orquestrador de compra: gerencia carrinho e pagamento PIX.",
    instruction=(
        "Voce coordena o fluxo de compra da loja Diotex.\n"
        "Delegue operacoes de carrinho (adicionar, remover, ver itens) ao cart_agent.\n"
        "Delegue finalizacao de pedido e geracao de PIX ao payment_agent.\n"
        "Mantenha o contexto entre as etapas e garanta que o cliente confirme antes de pagar."
    ),
    sub_agents=[cart_agent, payment_agent],
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
        "frete e pagamento ao checkout_orchestrator, status de pedido ao support_agent e operacoes administrativas ao admin_agent. "
        "Nunca permita que um cliente consulte pedido de outro numero. "
        "Nunca altere preco, descricao ou estoque fora do fluxo administrativo com OTP."
    ),
    sub_agents=[catalog_agent, sales_agent, checkout_orchestrator, support_agent, admin_agent],
)