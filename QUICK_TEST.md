# 🚀 Quick Start - Testes do Cleanup Scheduler

## 30 segundos para começar

### 1. Verificar se a API está rodando:
```bash
curl http://localhost:8000/health
```

Esperado: `{"status":"ok",...}`

### 2. Rodar os testes interativos:
```bash
python3 scripts/test_cleanup_interactive.py
```

Isso vai:
- ✓ Verificar se API está online
- ✓ Testar autenticação
- ✓ Testar em modo seguro (dry_run)
- ✓ Mostrar pedidos expirados (sem deletar)
- ✓ Validar parâmetros

## Testes principais

### Teste de segurança (dry_run = não deleta nada):
```bash
TOKEN=$(grep SCHEDULER_TOKEN .env | cut -d'=' -f2)
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50" \
  -H "X-Scheduler-Token: $TOKEN"
```

### Teste sem autenticação (deve dar erro):
```bash
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders"
# Retorna 401 Unauthorized
```

### Teste de produção (DELETA pedidos):
```bash
TOKEN=$(grep SCHEDULER_TOKEN .env | cut -d'=' -f2)
curl -X POST "http://localhost:8000/internal/maintenance/cleanup-expired-orders?dry_run=false&limit=200" \
  -H "X-Scheduler-Token: $TOKEN"
```

## Monitorar Cloud Scheduler

### Ver próxima execução:
```bash
gcloud scheduler jobs describe cleanup-expired-orders \
  --project=diotex-tecidos \
  --location=us-central1
```

### Ver logs da execução:
```bash
gcloud logging read "jsonPayload.event=cleanup_expired_orders" \
  --project=diotex-tecidos \
  --format="table(timestamp,jsonPayload.found,jsonPayload.deleted)"
```

## Estrutura de testes

```
✓ tests/test_cleanup_endpoint.py     - Testes automatizados (pytest)
✓ scripts/test_cleanup_interactive.py - Testes interativos (manual)
✓ TESTING.md                          - Documentação detalhada
✓ QUICK_TEST.md                       - Este arquivo
```

## Valores esperados

### Dry Run (seguro):
```json
{
  "dry_run": true,
  "found": 3,
  "deleted": 0,
  "orders": [{"order_id": 123, "status": "awaiting_payment", ...}]
}
```

### Produção (deleta):
```json
{
  "dry_run": false,
  "found": 3,
  "deleted": 3
}
```

## Mais informações

Leia `TESTING.md` para documentação completa e troubleshooting.
