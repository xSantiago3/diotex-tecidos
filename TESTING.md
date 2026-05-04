# Guia de Testes - Cleanup Scheduler Endpoint

## 📋 Visão Geral

Este diretório contém testes para o endpoint de limpeza de pedidos expirados (`/internal/maintenance/cleanup-expired-orders`) que está integrado com Cloud Scheduler.

## 🚀 Como Começar

### 1. Instalar Dependências

```bash
pip install pytest pytest-cov httpx requests python-dotenv
```

### 2. Configurar Variáveis de Ambiente

Crie um arquivo `.env.test` com:

```env
API_URL=http://localhost:8000
SCHEDULER_TOKEN=seu_token_aqui
FIRESTORE_ENABLED=true
```

Ou use o `.env` existente que já tem `SCHEDULER_TOKEN`.

### 3. Iniciar a API

Em um terminal:
```bash
uvicorn app.main:app --reload --port 8000
```

## 📝 Executar os Testes

### Opção 1: Teste Interativo (Recomendado para começar)

```bash
python3 scripts/test_cleanup_interactive.py
```

Este script roda testes interativos que:
- ✓ Verifica se a API está online
- ✓ Testa autenticação (token obrigatório)
- ✓ Testa rejeição de token inválido
- ✓ Executa cleanup em modo dry_run (seguro, sem deletar)
- ✓ Permite teste em produção (com confirmação)
- ✓ Valida parâmetros (min/max)
- ✓ Testa valores padrão

**Saída esperada:**
```
╔════════════════════════════════════════════════════════════╗
║    Testes Interativos - Cleanup Scheduler Endpoint        ║
║    Diotex Tecidos Commerce Backend                        ║
╚════════════════════════════════════════════════════════════╝

ℹ API URL: http://localhost:8000
ℹ Endpoint: http://localhost:8000/internal/maintenance/cleanup-expired-orders
✓ SCHEDULER_TOKEN: -Itt4kLDffYU_niqZpiPZvmerVf8Uwi2...

[Testes executando...]

✓ Health Check: PASS
✓ Auth Required: PASS
✓ Invalid Token: PASS
✓ Dry Run: PASS
✓ Parameter Validation: PASS
✓ Default Parameters: PASS

Resultado: 6/7 testes passaram
✓ Todos os testes passaram! ✓
```

### Opção 2: Testes com Pytest (Para CI/CD)

```bash
# Todos os testes
pytest tests/test_cleanup_endpoint.py -v

# Apenas autenticação
pytest tests/test_cleanup_endpoint.py::TestCleanupEndpointAuthentication -v

# Com cobertura
pytest tests/test_cleanup_endpoint.py --cov=app --cov-report=html

# Teste específico
pytest tests/test_cleanup_endpoint.py::TestCleanupEndpointAuthentication::test_cleanup_without_token_returns_401 -v
```

## 🧪 Casos de Teste Inclusos

### Test 1: Health Check
Verifica se a API está respondendo no endpoint `/health`

```bash
curl http://localhost:8000/health
# Esperado: {"status":"ok","app_name":"diotextecidos-agent-backend"}
```

### Test 2: Authentication Required
Verifica que chamar sem token retorna 401

```bash
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50"
# Esperado: 401 Unauthorized
```

### Test 3: Invalid Token Rejection
Verifica que token inválido é rejeitado

```bash
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50" \
  -H "X-Scheduler-Token: invalid-token"
# Esperado: 401 Unauthorized
```

### Test 4: Dry Run (Seguro)
Executa cleanup sem deletar nada (apenas lista)

```bash
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50" \
  -H "X-Scheduler-Token: seu_token_aqui"
# Esperado: 200 OK com lista de pedidos expirados
```

**Resposta esperada:**
```json
{
  "dry_run": true,
  "found": 3,
  "deleted": 0,
  "orders": [
    {
      "order_id": 123,
      "order_number": "DTX-20260504-000123",
      "status": "awaiting_payment",
      "expires_at": "2026-05-04T02:00:00Z",
      "last_modified_at": "2026-05-02T02:00:00Z"
    }
  ]
}
```

### Test 5: Production Run
Executa cleanup DE VERDADE (deleta pedidos expirados)

```bash
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=false&limit=200" \
  -H "X-Scheduler-Token: seu_token_aqui"
# Esperado: 200 OK com contagem de deletados
```

**Resposta esperada:**
```json
{
  "dry_run": false,
  "found": 3,
  "deleted": 3
}
```

### Test 6: Parameter Validation
Valida que parâmetros estão dentro de limites

- `limit` deve ser >= 1 e <= 1000
- Outros valores retornam 422

### Test 7: Default Parameters
Verifica valores padrão funcionam

- `dry_run` padrão: `false`
- `limit` padrão: `200`

## 📊 Verificação com curl

### Quick Test (sem autenticação):
```bash
# Deve retornar 401
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true"
```

### Quick Test (com autenticação):
```bash
# Substitua seu_token_aqui
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50" \
  -H "X-Scheduler-Token: seu_token_aqui"
```

### Ver todos os logs em tempo real:
```bash
# Cloud Run logs (production)
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=diotex-tecidos-api" \
  --project=diotex-tecidos \
  --limit=50 \
  --freshness=10m \
  --format="table(timestamp,severity,jsonPayload.message)"

# Local logs (development)
# Veja os logs no console da API
```

## 🔍 Monitorar Scheduler

### Ver job do scheduler:
```bash
gcloud scheduler jobs describe cleanup-expired-orders \
  --project=diotex-tecidos \
  --location=us-central1
```

### Ver histórico de execuções:
```bash
gcloud scheduler jobs describe cleanup-expired-orders \
  --project=diotex-tecidos \
  --location=us-central1 \
  --format="value(lastAttemptTime,status)"
```

### Ver logs da última execução:
```bash
gcloud logging read "resource.type=cloud_run_revision AND jsonPayload.event=cleanup_expired_orders" \
  --project=diotex-tecidos \
  --limit=10 \
  --format="table(timestamp,jsonPayload.found,jsonPayload.deleted)"
```

## 📈 Esperado vs Real

### Cenário 1: Nenhum pedido expirado
```json
{
  "dry_run": true,
  "found": 0,
  "deleted": 0,
  "orders": []
}
```

### Cenário 2: Pedidos expirados encontrados
```json
{
  "dry_run": true,
  "found": 5,
  "deleted": 0,
  "orders": [
    {"order_id": 1, "status": "awaiting_payment", ...},
    {"order_id": 2, "status": "awaiting_shipping_choice", ...}
  ]
}
```

### Cenário 3: Produção com deletados
```json
{
  "dry_run": false,
  "found": 5,
  "deleted": 5
}
```

## 🐛 Troubleshooting

### Erro: Connection refused
- **Causa:** API não está rodando
- **Solução:** `uvicorn app.main:app --reload --port 8000`

### Erro: 401 Unauthorized
- **Causa:** Token inválido ou ausente
- **Solução:** Verifique `SCHEDULER_TOKEN` no `.env`

### Erro: 422 Unprocessable Entity
- **Causa:** Parâmetros fora dos limites
- **Solução:** Verifique `limit` entre 1 e 1000, `dry_run` é bool

### Erro: 500 Internal Server Error
- **Causa:** Erro no servidor (Firestore, config, etc)
- **Solução:** Verifique logs: `gcloud logging read ...`

### Firestore não habilitado
- **Erro:** 400 Bad Request "Firestore desabilitado"
- **Solução:** `gcloud services enable firestore.googleapis.com --project=diotex-tecidos`

## 📚 Arquivos de Teste

```
tests/
├── test_cleanup_endpoint.py      # Testes com pytest (CI/CD)
└── conftest.py                    # Configuração pytest (opcional)

scripts/
└── test_cleanup_interactive.py    # Testes interativos (manual)
```

## 🎯 Próximos Passos

1. ✅ Rodar `python3 scripts/test_cleanup_interactive.py`
2. ✅ Verificar saída em modo dry_run
3. ✅ Monitorar Cloud Scheduler (próxima execução em 1h)
4. ✅ Adicionar testes para suas funcionalidades customizadas
5. ✅ Integrar testes em CI/CD (GitHub Actions, GitLab CI, etc)

## 📞 Suporte

Para dúvidas sobre os testes, consulte:
- [FastAPI TestClient Docs](https://fastapi.tiangeo.com/advanced/testing-websockets/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Cloud Scheduler Docs](https://cloud.google.com/scheduler/docs)
