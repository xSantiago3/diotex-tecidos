# Instruções do Copilot — diotextecidos

## Regra Principal: Build & Deploy

**NUNCA execute `gcloud builds submit` ou `gcloud run services update` sem autorização explícita.**

Após fazer alterações no código, sempre pergunte:
> "As mudanças estão prontas. Posso fazer build e deploy agora?"

Só execute após o usuário confirmar com algo como "pode", "sim", "faz o deploy", etc.

### Aguardando Build/Deploy

- **NUNCA** faça polling repetido do status do build. Dispare o comando em modo async e **aguarde a notificação do terminal** chegar.
- Só verifique manualmente o status se passarem **mais de 3 minutos** sem notificação — e faça isso **uma única vez**.
- ❌ Proibido chamar `gcloud builds describe`, `gcloud run services describe` ou similares em loop para monitorar progresso.

## Projeto

- **Serviço**: `diotex-tecidos-api` (Google Cloud Run)
- **Projeto GCP**: `diotex-tecidos`
- **Região**: `us-central1`
- **Ambiente atual**: desenvolvimento (sandbox)

## Configurações de Ambiente (Dev/Sandbox)

- `MELHOR_ENVIO_BASE_URL` = `https://sandbox.melhorenvio.com.br`
- `ME_SENDER_DOCUMENT` = `36600804890` (CPF do perfil sandbox)
- `MELHOR_ENVIO_TOKEN` = token do sandbox (Secret Manager)
- `FIRESTORE_ENABLED` = `true`
- `GOOGLE_CLOUD_PROJECT` = `diotex-tecidos`

## Comandos Úteis

```bash
# Build
gcloud builds submit --tag gcr.io/diotex-tecidos/diotex-tecidos-api:latest --project diotex-tecidos

# Deploy
gcloud run services update diotex-tecidos-api --project=diotex-tecidos --region=us-central1 --image=gcr.io/diotex-tecidos/diotex-tecidos-api:latest

# Reset sessão WhatsApp
curl -sS -X POST "https://diotex-tecidos-api-298996329023.us-central1.run.app/internal/reset-session/5511982732814" \
  -H "X-Scheduler-Token: <scheduler_token>"
```
## Filosofia de Mensagens ao Cliente

**O LLM deve gerar todas as mensagens ao cliente — nunca use heurísticas, regex ou strings hardcoded para isso.**

- ✅ Instrua o agente via prompt, system instructions e few-shot examples
- ✅ Passe contexto estruturado ao agente e deixe ele decidir o que dizer
- ❌ Nunca construa strings de resposta com f-string/concatenação para enviar ao cliente
- ❌ Regex e parsing de texto só como último recurso (ex: extração de dados de sistemas externos)
- ❌ Nunca envie mensagem de erro técnico ao cliente (ex: "Desculpe, tive um problema técnico")
## Outros Lembretes

- Depois de cada deploy bem-sucedido, resetar automaticamente a sessão WhatsApp para teste.
- Pode encadear o reset no mesmo comando de deploy (com `&&`) para executar em sequência.
- Não commitar `.env` nem segredos no git.
- Logs do Cloud Run: `gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=diotex-tecidos-api" --project=diotex-tecidos --limit=50 --freshness=10m`
