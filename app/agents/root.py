from __future__ import annotations

from google.adk.agents import Agent

from app.config import get_settings


settings = get_settings()

catalog_agent = Agent(
    name="catalog_agent",
    model=settings.default_model,
    description="Consulta produtos, preco, composicao e fotos.",
    instruction=(
        "Voce atende uma loja de tecidos. Responda duvidas sobre catalogo, atributos, preco e fotos. "
        "Nunca altere preco, descricao, estoque ou regras comerciais. "
        "Se o cliente pedir imagens, informe que o sistema deve anexar as imagens do produto correspondente."
    ),
)

sales_agent = Agent(
    name="sales_agent",
    model=settings.default_model,
    description="Conduz pre-venda, comparacoes e recomendacoes.",
    instruction=(
        "Voce ajuda o cliente a escolher tecidos para moda, decoracao e artesanato. "
        "Nao conceda descontos e nao prometa nada fora das regras comerciais. "
        "Quando houver fechamento de compra, encaminhe para checkout."
    ),
)

checkout_agent = Agent(
    name="checkout_agent",
    model=settings.default_model,
    description="Coleta CEP, calcula frete e conduz pagamento.",
    instruction=(
        "Voce coleta CEP, prepara cotacao de frete e apresenta prazo da transportadora acrescido de 2 dias de preparacao. "
        "Explique que o envio pode ocorrer no mesmo dia quando houver disponibilidade operacional. "
        "Se o pedido exceder limite de peso ou dimensoes, oriente a dividir o pedido."
    ),
)

support_agent = Agent(
    name="support_agent",
    model=settings.default_model,
    description="Acompanha status de pedido e orienta pos-venda.",
    instruction=(
        "Voce responde duvidas de status e pos-venda. "
        "So informe status de pedido quando o numero do cliente corresponder ao dono do pedido."
    ),
)

admin_agent = Agent(
    name="admin_agent",
    model=settings.default_model,
    description="Executa fluxos administrativos autorizados.",
    instruction=(
        "Voce processa comandos administrativos de preco e estoque apenas para numeros autorizados. "
        "Sempre exija OTP antes de confirmar qualquer escrita."
    ),
)

root_agent = Agent(
    name="diotex_orchestrator",
    model=settings.default_model,
    description="Orquestrador para atendimento multiagente da Diotex Tecidos.",
    instruction=(
        "Voce e o orquestrador da Diotex Tecidos. "
        "Atenda em portugues por padrao e mude para espanhol ou ingles quando o cliente pedir ou quando detectar isso claramente. "
        "Delegue consultas de catalogo ao catalog_agent, recomendacoes e vendas ao sales_agent, "
        "frete e pagamento ao checkout_agent, status ao support_agent e operacoes autorizadas ao admin_agent. "
        "Nunca permita que um cliente consulte pedido de outro numero. "
        "Nunca altere preco, descricao ou estoque fora do fluxo administrativo com OTP."
    ),
    sub_agents=[catalog_agent, sales_agent, checkout_agent, support_agent, admin_agent],
)