# Diotex Tecidos - Agent Backend

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-green)](https://fastapi.tiangolo.com/)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-1.32%2B-yellow)](https://cloud.google.com/developers/agents)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Sistema automatizado de atendimento multi-canal para e-commerce de têxteis, integrando WhatsApp, WooCommerce, Mercado Pago e serviços de logística através de múltiplos agentes de IA coordenados.

## 🎯 Características

- **Orquestração Multi-Agentes**: 8 agentes especializados com Google ADK
- **Integração WhatsApp**: Atendimento automático via WhatsApp Business
- **E-commerce**: Sincronização com WooCommerce
- **Pagamentos**: Mercado Pago (sandbox e produção)
- **Logística**: Geração de etiquetas com Melhor Envio
- **Persistência**: CPF de cliente, histórico e perfil salvo
- **Administração**: Endpoints protegidos com OTP

## 📋 Pré-requisitos

- Python 3.11+
- Conta Google Cloud com projeto ativo
- WhatsApp Business via Meta
- WooCommerce API (opcional)
- Mercado Pago (teste ou produção)
- Melhor Envio (teste ou produção)

## 🚀 Quick Start

### Instalação Local

```bash
# Clone o repositório
git clone https://github.com/xSantiago3/diotex-tecidos.git
cd diotex-tecidos

# Crie um ambiente virtual
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Instale dependências
pip install -e .

# Configure variáveis de ambiente
cp .env.example .env
# Edite .env com suas credenciais

# Inicie o servidor
uvicorn app.main:app --reload
```

A API estará em `http://localhost:8000` | Docs em `http://localhost:8000/docs`

### Docker

```bash
docker build -t diotex-tecidos-api .
docker run -p 8000:8000 --env-file .env diotex-tecidos-api
```

## 🏗️ Arquitetura

```
┌─────────────────────────────────────┐
│     WhatsApp / Web Interface        │
└────────────────┬────────────────────┘
                 │
    ┌────────────▼─────────────────┐
    │      FastAPI Gateway         │
    └────────────┬─────────────────┘
                 │
    ┌────────────┴──────────────────────────┐
    │                                       │
    ▼                                       ▼
┌──────────────────┐              ┌─────────────────────┐
│  Root Agent      │              │ Session Manager     │
│  (Google ADK)    │              │ (Vertex AI)         │
└────┬─┬──┬──┬────┘              └─────────────────────┘
     │ │  │ │
     │ ▼  │ └──► Checkout Agent + Payment Agent ◄─── Mercado Pago
     │    │
     ▼    ▼
   Catalog  Sales Agents
   Agent

        │
        ▼
┌─────────────────────────┐
│  Persistence Layer      │
│ - SQLModel + SQLAlchemy │
│ - PostgreSQL (Prod)     │
│ - SQLite (Dev)          │
│ - Firestore (Opcional)  │
└────────┬────────────────┘
         │
    ┌────┼─────────┐
    ▼    ▼         ▼
WooCommerce  Melhor  Mercado
             Envio   Livre
```

## 🔧 Configuração

### Variáveis de Ambiente

```bash
cp .env.example .env
```

**Essenciais:**

```env
# Google Cloud
GOOGLE_CLOUD_PROJECT=seu-projeto
GOOGLE_CLOUD_LOCATION=us-central1

# WhatsApp/Meta
META_VERIFY_TOKEN=seu-token
META_APP_SECRET=seu-secret
META_WHATSAPP_ACCESS_TOKEN=seu-token
META_WHATSAPP_PHONE_NUMBER_ID=seu-phone-id

# Serviços Externos
MELHOR_ENVIO_TOKEN=seu-jwt
ME_SENDER_DOCUMENT=seu-cpf
MERCADO_PAGO_ACCESS_TOKEN=seu-token

# Admin
ADMIN_ALLOWED_PHONES=+5511999999999
NOTIFICATION_PHONE=+5511999999999
```

**⚠️ SEGURANÇA**: Nunca commite `.env`. Use Google Secret Manager em produção.

## 📡 API Endpoints

### Status
- `GET /health` - Health check
- `GET /status` - Status da aplicação

### Catálogo (Público)
- `GET /catalog/products` - Lista com busca e paginação
- `GET /catalog/products/{id}` - Detalhes do produto
- `POST /shipping/quote` - Cálculo de frete

### Cliente
- `GET /customers/{phone}/orders` - Histórico
- `GET /customers/{phone}/orders/{id}` - Detalhes
- `GET /customers/{phone}/profile` - Perfil salvo

### WhatsApp
- `GET /webhooks/whatsapp` - Validação
- `POST /webhooks/whatsapp` - Recebe mensagens

### Pagamentos (Webhook)
- `POST /webhooks/mercadopago` - Notificações

### Admin (Requer OTP)
- `POST /admin/sync/woocommerce` - Sincroniza produtos
- `POST /internal/reset-session/{phone}` - Limpa sessão (dev)

**Documentação Interativa:** http://localhost:8000/docs

## 🗄️ Banco de Dados

### Modelos Principais

```python
Customer
├── whatsapp_phone (PK)
├── name
├── email
├── cpf (indexado)
├── zipcode
├── address_number
└── created_at

Order
├── id (PK)
├── customer_id (FK)
├── status
├── items
├── payment_method
├── mercadopago_order_id
└── total_price

Product
├── id (PK)
├── woo_product_id
├── name, price
├── weight_g, dimensions
└── images[]
```

### Migração para PostgreSQL

```bash
python -m app.cloudsql_migrate \
  --source sqlite:///./diotextecidos.db \
  --target 'postgresql+psycopg://user:pass@host/db'
```

## 🤖 Agentes

| Agente | Função |
|--------|--------|
| `root_agent` | Orquestrador principal |
| `sales_agent` | Recomendações de produtos |
| `catalog_agent` | Detalhes de catálogo |
| `checkout_orchestrator` | Coordena carrinho + pagamento |
| `cart_agent` | Gerenciamento do carrinho |
| `payment_agent` | Processamento de pagamento (8 etapas) |
| `support_agent` | FAQ e suporte |
| `admin_agent` | Operações administrativas |

### Fluxo de Checkout

1. ✓ Confirmar carrinho
2. ✓ Recuperar perfil salvo ("Esses dados continuam?")
3. ✓ Coletar dados se necessário (CEP, nome, email, CPF, número)
4. ✓ Calcular opções de frete
5. ✓ Escolher opção de envio
6. ✓ Escolher método de pagamento
7. ✓ Finalizar pagamento (CPF e número obrigatórios)
8. ✓ Confirmação e geração de etiqueta

## 💳 Mercado Pago

### Teste

```env
MERCADO_PAGO_ACCESS_TOKEN=TEST-1234567890...
```

- URL Sandbox: `https://sandbox.mercadopago.com.br/...`
- Cartões de teste fornecidos por MP

### Produção

```env
MERCADO_PAGO_ACCESS_TOKEN=APP-1234567890...
```

- URL Real: `https://www.mercadopago.com.br/...`

## 📮 Integração Melhor Envio

Após pagamento aprovado:

1. Valida CPF/CNPJ do remetente
2. Prepara dados do cliente
3. Calcula dimensões
4. Gera etiqueta
5. Envia link de rastreamento

## 🚢 Deploy em Google Cloud

### Cloud Run

```bash
# Build
gcloud builds submit \
  --tag gcr.io/PROJECT/diotex-tecidos-api:latest

# Deploy
gcloud run services update diotex-tecidos-api \
  --image gcr.io/PROJECT/diotex-tecidos-api:latest \
  --region us-central1 \
  --project PROJECT
```

### Secrets

```bash
echo "seu-token" | gcloud secrets create MERCADO_PAGO_TOKEN --data-file=-
```

### Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND \
   resource.labels.service_name=diotex-tecidos-api" \
  --limit=50
```

## 🧪 Testes

```bash
pytest tests/
pytest --cov=app tests/
```

## 🔐 Segurança

- ✅ Environment variables via Secret Manager
- ✅ OTP para operações admin
- ✅ Controle de acesso por WhatsApp
- ✅ CORS configurado
- ✅ Validação de webhook

**Práticas recomendadas:**
- Use HTTPS em produção
- Configure Cloud Armor para DDoS
- Revise logs regularmente
- Rotacione tokens periodicamente

## 📚 Documentação Adicional

- **Swagger**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI**: http://localhost:8000/openapi.json
- **Google ADK**: https://cloud.google.com/developers/agents
- **FastAPI**: https://fastapi.tiangolo.com/

## 🤝 Contribuindo

1. Fork o projeto
2. Crie uma branch para sua feature (`git checkout -b feature/NewFeature`)
3. Commit suas mudanças (`git commit -m 'Add NewFeature'`)
4. Push para a branch (`git push origin feature/NewFeature`)
5. Abra um Pull Request

## 📝 Licença

MIT - veja [LICENSE](LICENSE) para detalhes

## 📧 Suporte

- [Issues no GitHub](https://github.com/xSantiago3/diotex-tecidos/issues)
- WhatsApp Business

## 🙏 Tecnologias

- [Google ADK](https://cloud.google.com/developers/agents) - Orquestração
- [FastAPI](https://fastapi.tiangolo.com/) - API Web
- [SQLModel](https://sqlmodel.tiangolo.com/) - ORM
- [Vertex AI Gemini](https://cloud.google.com/vertex-ai) - LLM

---

**Última atualização**: Maio de 2026  
**Status**: Em Produção ✅