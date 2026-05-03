# Diotex Tecidos Agent Backend

Backend inicial para atendimento automatizado no site e no WhatsApp usando Google ADK, FastAPI e SQLModel.

## O que este scaffold cobre

- cadastro de clientes vinculado ao numero de WhatsApp;
- pedidos vinculados ao cliente para consulta de status com controle de acesso por numero;
- agentes especializados com um orquestrador ADK;
- esqueleto de webhook do WhatsApp;
- modelo para comandos administrativos com OTP;
- base pronta para importar produtos do WooCommerce.

## Stack

- Python 3.11+
- Google ADK
- Vertex AI com Gemini 2.5 Flash em producao
- FastAPI
- SQLModel
- SQLite no desenvolvimento local e Cloud SQL PostgreSQL em producao

## Como rodar

1. Crie um ambiente virtual.
2. Instale as dependencias com `pip install -e .`.
3. Copie `.env.example` para `.env` e preencha as chaves.
4. Rode `uvicorn app.main:app --reload`.

## Producao Google Cloud

Destino recomendado:

- Cloud Run para a API
- Vertex AI para os agentes com Gemini 2.5 Flash
- Cloud SQL for PostgreSQL para o banco principal
- Secret Manager para tokens e credenciais

Variaveis principais de producao:

- `GOOGLE_GENAI_USE_VERTEXAI=true`
- `GOOGLE_CLOUD_PROJECT=seu-projeto`
- `GOOGLE_CLOUD_LOCATION=us-central1`
- `DEFAULT_MODEL=gemini-2.5-flash`
- `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE`

Observacao importante:

- os produtos importados ate agora estao somente no banco local `diotextecidos.db`
- eles ainda nao foram enviados para nenhum banco no Google Cloud
- quando o Cloud SQL estiver criado, eu consigo adaptar a carga para subir essa mesma base la

## Migracao para Cloud SQL PostgreSQL

Script incluido:

- `python -m app.cloudsql_migrate --source-url sqlite:///./diotextecidos.db --target-url 'postgresql+psycopg://USER:PASSWORD@HOST/DBNAME'`

Para Cloud SQL via Unix socket no Cloud Run, o alvo costuma ficar assim:

```bash
postgresql+psycopg://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE
```

## Importar produtos

Quando voce mandar a tabela atual do site, o importador ja aceita CSV ou JSON.

- Exemplo CSV/JSON: `python -m app.import_products caminho/do/arquivo.csv`
- Campos suportados: `woo_product_id`, `name` ou `nome`, `description` ou `descricao`, `price` ou `preco`, `width_cm` ou `largura_cm`, `composition` ou `composicao`, `color` ou `cor`, `unit_type` ou `unidade`, `active` ou `ativo`

### Mapeamento WooCommerce usado

- `Peso (g)` -> `weight_g` (peso do tecido)
- `Comprimento (cm)` -> `package_length_cm` (produto embalado)
- `Largura (cm)` -> `package_width_cm` (produto embalado)
- `Altura (cm)` -> `package_height_cm` (produto embalado)
- `Imagens` -> tabela `product_images`

## Endpoints iniciais

- `GET /health`
- `GET /customers/{whatsapp_phone}/orders/{order_id}`
- `GET /catalog/products`
- `POST /admin/sync/woocommerce`
- `POST /shipping/quote`
- `GET /webhooks/whatsapp`
- `POST /webhooks/whatsapp`

## Sync automatico WooCommerce

Endpoint:

- `POST /admin/sync/woocommerce`

Esse endpoint busca os produtos via API do WooCommerce e atualiza a base local (incluindo imagens).

## Catalogo para chatbot/site

Endpoint:

- `GET /catalog/products?search=oxford&limit=20&offset=0`

Resposta inclui nome, preco, estoque disponivel e URLs de imagem.

## Cotacao de frete

Endpoint:

- `POST /shipping/quote`

### Exemplo Melhor Envio

```json
{
	"provider": "melhor_envio",
	"to_zipcode": "01310-100",
	"product_name": "Oxford Amarelo (1x1,50m)",
	"quantity": 2,
	"unit_price": 8.99,
	"weight_g": 200,
	"package_length_cm": 26,
	"package_width_cm": 36,
	"package_height_cm": 4
}
```

### Exemplo Mercado Livre

```json
{
	"provider": "mercado_livre",
	"to_zipcode": "01310-100",
	"product_name": "Oxford Amarelo (1x1,50m)",
	"quantity": 1,
	"unit_price": 8.99,
	"weight_g": 200,
	"package_length_cm": 26,
	"package_width_cm": 36,
	"package_height_cm": 4,
	"mercado_livre_item_id": "MLB1234567890"
}
```

No retorno, o prazo tambem vem com `delivery_days_with_preparation`, que soma os 2 dias extras de preparacao.

## Checkout de carrinho

Endpoint:

- `POST /checkout/quote`

Comportamento:

- se o cliente nao tiver CEP salvo e nao mandar CEP, a API responde pedindo CEP
- se o cliente mandar CEP, o CEP e salvo no cadastro do cliente
- o endpoint calcula frete automaticamente com Melhor Envio, cria um pedido rascunho e devolve subtotal, frete e total final

Exemplo:

```json
{
	"whatsapp_phone": "+5511988887777",
	"zipcode": "01310-100",
	"customer_name": "Cliente Teste",
	"items": [
		{
			"product_id": 1,
			"quantity": 2
		}
	]
}
```

## Cloud Run

Arquivos preparados:

- `Dockerfile`
- `.dockerignore`
- `scripts/deploy_cloud_run.sh`

Exemplo de build local da imagem:

```bash
docker build -t diotextecidos-agent-backend .
```

Exemplo de deploy:

```bash
chmod +x scripts/deploy_cloud_run.sh
GOOGLE_CLOUD_PROJECT=diotex-tecidos GOOGLE_CLOUD_LOCATION=us-central1 ./scripts/deploy_cloud_run.sh
```

## Vertex AI / ADK

Endpoint simples de chat do orquestrador:

- `POST /agent/chat`

Exemplo:

```json
{
	"user_id": "5511988887777",
	"session_id": "sessao-1",
	"message": "Quais tecidos voce recomenda para vestido leve?"
}
```

Com `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=diotex-tecidos`, `GOOGLE_CLOUD_LOCATION=us-central1` e credenciais ADC no ambiente, o ADK usa o Gemini 2.5 Flash via Vertex AI.

## Publicacao no GitHub

O projeto foi sanitizado para publicacao com `.gitignore` protegendo `.env`, banco local e artefatos.

Limite atual deste ambiente:

- nao ha `gh` instalado nem autenticacao GitHub configurada aqui
- por isso eu consigo deixar o repositorio pronto localmente, mas nao consigo criar o remoto e fazer push sem credenciais

## Regras de seguranca implementadas no scaffold

- cliente so consulta pedidos do proprio numero de WhatsApp;
- numero admin autorizado inicial: `+5511982732814`;
- alteracoes administrativas exigem OTP antes de serem efetivadas;
- o agente publico nao altera preco nem descricao.