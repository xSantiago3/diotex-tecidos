# 📱 Guia de Testes via WhatsApp

## 🎯 Visão Geral

Este guia mostra como testar toda a API da Diotex Tecidos através do WhatsApp Business, que é o principal canal de interação com clientes.

## ✅ Pré-requisitos

1. ✓ API em produção (Cloud Run)
2. ✓ WhatsApp Business Account configurado
3. ✓ Número de telefone de teste (seu próprio número)
4. ✓ App de WhatsApp instalado no celular
5. ✓ Acesso aos logs da API (Cloud Logging)

## 🔧 Configuração Inicial

### 1. Verificar Webhook está Ativo

```bash
# Ver status do webhook
gcloud run services describe diotex-tecidos-api \
  --project=diotex-tecidos \
  --region=us-central1 \
  --format="value(status.url)"

# Saída esperada:
# https://diotex-tecidos-api-298996329023.us-central1.run.app
```

### 2. Adicionar Seu Número como Teste

```bash
# Seu número deve estar no grupo de números permitidos
# Normalmente: +55 11 98273-2814 (o seu)
# Ou adicione em app/config.py na variável ADMIN_ALLOWED_PHONES
```

### 3. Monitorar Logs em Tempo Real

```bash
# Em um terminal separado, deixe rodando:
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=diotex-tecidos-api" \
  --project=diotex-tecidos \
  --follow \
  --format="value(timestamp,jsonPayload.severity,textPayload)"
```

## 📝 Passo a Passo de Testes

### TESTE 1: Enviando Mensagem de Saudação

**O que testar:** A API reconhece mensagens e retorna resposta

**Passos:**
1. Abra WhatsApp no celular
2. Procure pelo número: `+55 11 XXXX-XXXX` (seu número de teste)
3. Envie uma mensagem:
   ```
   Oi
   ```

**Esperado:**
- ✓ Resposta da API em 2-5 segundos
- ✓ Bot responde com mensagem de boas-vindas
- ✓ Exemplo: "Olá! Bem-vindo à Diotex Tecidos. Como posso ajudar?"

**Logs esperados:**
```
2026-05-04T03:00:00Z  INFO     message_received event=agent_message message="Oi"
2026-05-04T03:00:01Z  INFO     message_sent event=agent_response status=sent
```

---

### TESTE 2: Buscar Produtos

**O que testar:** Busca por catálogo funciona

**Passos:**
1. Envie:
   ```
   Quais tecidos vocês têm?
   ```

**Esperado:**
- ✓ Bot responde com opções de busca
- ✓ Mostra categoria sugerida (Helança, Viscose, etc)
- ✓ Oferece visualizar imagens

**Variações:**
```
- "Mostre helanca rosa"
- "Quais são as cores disponíveis?"
- "Tem algodão?"
- "Preço de viscose?"
```

**Logs esperados:**
```
INFO     search_products query="helanca rosa" results_found=5
INFO     send_catalog_media phone=551198273281 count=5
```

---

### TESTE 3: Visualizar Imagens (Midia)

**O que testar:** Envio de imagens funciona

**Passos:**
1. Envie:
   ```
   Mostrar helanca rosa com imagens
   ```

**Esperado:**
- ✓ Bot envia imagens dos produtos
- ✓ Cada imagem com descrição do tecido
- ✓ Preço por metro

**Logs esperados:**
```
INFO     send_catalog_media with_images=true count=3
INFO     whatsapp_media_sent media_type=image url=https://...
```

---

### TESTE 4: Iniciar Checkout

**O que testar:** Fluxo de compra começa corretamente

**Passos:**
1. Procure produto e clique em botão "Comprar" (se houver)
2. Ou envie:
   ```
   Quero comprar 2 metros de helanca rosa
   ```

**Esperado:**
- ✓ Bot confirma quantidade
- ✓ Pede CEP para calcular frete
- ✓ Mostra opções de entrega

**Exemplo de resposta:**
```
Perfeito! Vou calcular o frete.
Qual é seu CEP? (exemplo: 01001-000)
```

**Logs esperados:**
```
INFO     cart_item_added quantity=2 product_id=123
INFO     checkout_started order_id=UUID
```

---

### TESTE 5: Enviar CEP e Calcular Frete

**O que testar:** Integração com Melhor Envio

**Passos:**
1. Responda com seu CEP:
   ```
   01001-000
   ```

**Esperado:**
- ✓ Bot calcula frete
- ✓ Mostra opções de transportadora (Sedex, PAC, etc)
- ✓ Preço e prazo para cada opção

**Exemplo de resposta:**
```
Ótimo! Calculei o frete:

📦 SEDEX - R$ 45,90 (2 dias)
📦 PAC - R$ 25,50 (5 dias)
📦 Agendado - R$ 35,00 (1 dia)

Qual você prefere?
```

**Logs esperados:**
```
INFO     melhor_envio_quote quote_request shipping_cep=01001000
INFO     shipping_options found=3
```

---

### TESTE 6: Selecionar Método de Entrega

**O que testar:** Validação de dados de entrega

**Passos:**
1. Escolha uma opção (ex: clique em botão ou envie):
   ```
   SEDEX
   ```

**Esperado:**
- ✓ Bot confirma escolha
- ✓ Pergunta por dados de entrega (endereço)
- ✓ Ou oferece usar cadastro anterior

**Exemplo de resposta:**
```
Endereço de entrega:
Rua Exemplo, 123
Apt 456
Complemento: Apto com interfone
Cidade: São Paulo
Estado: SP
CEP: 01001-000

Confirma?
```

---

### TESTE 7: Selecionar Método de Pagamento

**O que testar:** Integração com Mercado Pago e PIX

**Passos:**
1. Confirme o endereço
2. Bot deve oferecer:
   ```
   Métodos de pagamento:
   💳 Mercado Pago
   💰 PIX
   📱 Pix instantâneo (Copia e Cola)
   ```

3. Escolha:
   ```
   PIX
   ```

**Esperado:**
- ✓ Bot gera QR Code ou Chave PIX
- ✓ Mostra valor total
- ✓ Instruções de pagamento

**Exemplo de resposta:**
```
Método selecionado: PIX

Total: R$ 156,40

Chave: 12345678-1234-1234-1234-123456789012
Ou escaneie o QR Code

Enviando link Mercado Pago...
[Link com QR Code]

Após pagar, confirme aqui: "Paguei"
```

**Logs esperados:**
```
INFO     payment_method_selected method=pix
INFO     payment_link_generated order_id=123 amount=156.40
INFO     mercadopago_qr_generated
```

---

### TESTE 8: Confirmação de Pagamento

**O que testar:** Status de pedido atualiza

**Passos:**
1. Fazer pagamento PIX real OU
2. Simular confirmação:
   ```
   Paguei
   ```

**Esperado:**
- ✓ Bot reconhece pagamento
- ✓ Gera número do pedido (ex: DTX-20260504-000123)
- ✓ Envia informações de rastreamento
- ✓ Informa próximos passos

**Exemplo de resposta:**
```
✅ Pagamento confirmado!

Seu pedido: DTX-20260504-000123
Data: 04/05/2026 03:15

Itens:
- 2m Helanca Rosa Chiclete - R$ 47,90

Frete SEDEX: R$ 45,90
Total: R$ 156,40

Sua encomenda será preparada em breve.
Você receberá um link de rastreamento por aqui.

Obrigado! 🎉
```

**Logs esperados:**
```
INFO     order_status_updated order_id=123 status=paid
INFO     order_confirmation_sent template=pedido_confirmado
INFO     event=order_created order_number=DTX-20260504-000123
```

---

### TESTE 9: Rastreamento

**O que testar:** Cliente consegue rastrear pedido

**Passos:**
1. Envie:
   ```
   Rastrear DTX-20260504-000123
   ```

**Esperado:**
- ✓ Bot mostra status do pedido
- ✓ Data de envio
- ✓ Código de rastreamento da transportadora

**Exemplo de resposta:**
```
📦 Rastreamento do Pedido DTX-20260504-000123

Status: 📍 Enviado
Saída: 04/05/2026 15:30
Previsão: 06/05/2026

Rastreador: BR123456789BR
Link: https://www.melhorenvio.com.br/tracking/...

Clique no link para atualização em tempo real.
```

---

### TESTE 10: Suporte/Chat

**O que testar:** Escalação para atendimento

**Passos:**
1. Envie:
   ```
   Falar com atendente
   ```
   ou
   ```
   Tenho uma dúvida
   ```

**Esperado:**
- ✓ Bot entende intenção de suporte
- ✓ Oferece FAQ (respostas rápidas)
- ✓ Opção para falar com humano
- ✓ Admin recebe notificação

**Logs esperados:**
```
INFO     support_request received
INFO     admin_notification sent to=[+5511982732814]
```

---

## 📊 Testes Avançados

### TESTE 11: Múltiplas Compras no Mesmo Carrinho

**O que testar:** Carrinho persiste entre mensagens

**Passos:**
```
1. Envie: "Quero 2m helanca rosa"
2. Espere resposta
3. Envie: "Adiciona 3m viscose azul também"
4. Envie: "Quanto vai ficar no total?"
```

**Esperado:**
- ✓ Carrinho tem 2 itens
- ✓ Bot calcula total correto
- ✓ Histórico salvo em Firestore

---

### TESTE 12: Erro de Conexão / Retry

**O que testar:** Resiliência

**Passos:**
1. Envie mensagem
2. Desligue internet por 5 segundos
3. Ligue de novo

**Esperado:**
- ✓ Mensagem é retentada
- ✓ Não perde contexto de conversa

---

### TESTE 13: Imagens Repetidas (Evitar Duplicação)

**O que testar:** Sistema não envia mesma imagem 2x

**Passos:**
```
1. Envie: "Mostre helanca com imagens"
2. Espere
3. Envie: "Mostre helanca com imagens" (novamente)
```

**Esperado:**
- ✓ Na primeira vez: envia 5 imagens
- ✓ Na segunda vez: reconhece que já enviou
- ✓ Pergunta se quer ver outras cores

**Logs esperados:**
```
INFO     media_already_sent_to_user skip=true
```

---

### TESTE 14: Ordem sem Autenticação

**O que testar:** Apenas números permitidos podem encomendar

**Passos:**
1. De outro número (amigo), envie mensagem
2. Tente fazer pedido

**Esperado:**
- ✓ Sistema reconhece número novo
- ✓ Pode ver catálogo
- ✓ Pode solicitar checkout
- ✓ Pedido é criado com novo número

---

### TESTE 15: Admin Commands (OTP)

**O que testar:** Autenticação admin e funções privilegiadas

**Passos:**
1. Do seu número (admin), envie:
   ```
   admin:otp:start
   ```

**Esperado:**
- ✓ Sistema gera código OTP
- ✓ Código enviado por SMS ou no chat

2. Envie código:
   ```
   123456
   ```

**Esperado:**
- ✓ Autenticação bem-sucedida
- ✓ Acesso a comandos admin

---

## 🔍 Monitorar Testes

### Ver Logs em Tempo Real:

```bash
# Todos os logs
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=diotex-tecidos-api" \
  --project=diotex-tecidos 
  --follow

# Apenas eventos de pedido
gcloud logging read \
  "jsonPayload.event=order_created OR jsonPayload.event=payment_received" \
  --project=diotex-tecidos \
  --format="table(timestamp,jsonPayload.event,jsonPayload.order_id)"

# Apenas erros
gcloud logging read \
  "severity>=ERROR AND resource.labels.service_name=diotex-tecidos-api" \
  --project=diotex-tecidos \
  --format="table(timestamp,severity,textPayload)"
```

### Ver Pedidos Criados:

```bash
# No Firebase Console
# Firestore > orders > lista de pedidos recentes
# Ou via CLI:
gcloud firestore documents list --collection-id=orders --project=diotex-tecidos
```

---

## ✅ Checklist de Testes Completo

- [ ] **Básico**
  - [ ] Mensagem de saudação funciona
  - [ ] Bot responde em < 5 segundos
  - [ ] Logs aparecem em Cloud Logging

- [ ] **Catálogo**
  - [ ] Busca de produtos funciona
  - [ ] Imagens são enviadas
  - [ ] Preços corretos
  - [ ] Não duplica imagens

- [ ] **Checkout**
  - [ ] Carrin persiste entre mensagens
  - [ ] CEP é validado
  - [ ] Frete é calculado
  - [ ] Endereço é salvo

- [ ] **Pagamento**
  - [ ] PIX QR Code gerado
  - [ ] Link Mercado Pago funciona
  - [ ] Pagamento é confirmado
  - [ ] Status muda para "paid"

- [ ] **Pedido**
  - [ ] Número do pedido gerado (DTX-...)
  - [ ] Email de confirmação enviado
  - [ ] Rastreamento disponível
  - [ ] Admin recebe notificação

- [ ] **Suporte**
  - [ ] Escalação para admin funciona
  - [ ] FAQ oferecidas
  - [ ] Chat de suporte disponível

- [ ] **Resiliência**
  - [ ] Reconecta após desconexão
  - [ ] Não perde contexto
  - [ ] Retentativas funcionam

---

## 🐛 Troubleshooting

### Problema: Não recebo resposta no WhatsApp

**Causa possível:** API não está rodando ou webhook não está configurado

```bash
# Verificar status
curl -s https://diotex-tecidos-api-298996329023.us-central1.run.app/health | jq

# Ver logs de erro
gcloud logging read severity>=ERROR --project=diotex-tecidos --limit=20
```

### Problema: Mensagem demora muito

**Causa possível:** Firestore não está respondendo ou Gemini está lento

```bash
# Ver latência
gcloud logging read "textPayload~'duration'" --limit=10 --format=json | jq '.[] | .duration'
```

### Problema: Imagens não são enviadas

**Causa possível:** URLs de imagem inválidas ou sem acesso

```bash
# Ver erro de media
gcloud logging read "textPayload~'media'" --limit=10 --project=diotex-tecidos
```

### Problema: Pagamento não é confirmado

**Causa possível:** Webhook do Mercado Pago não está recebendo notificações

```bash
# Verificar webhook
gcloud logging read "jsonPayload.event=mercadopago_webhook" --project=diotex-tecidos
```

---

## 📱 Dicas Rápidas

### Acelerar Testes:

1. **Use seu número pessoal** como primeiro testador
2. **Deixe logs abertos** em terminal separado
3. **Use dry_run=true** para testes sem efeitos colaterais
4. **Capture IDs de teste** para rastrear depois

### Exemplo de ID tracking:

```bash
# Enviar para si mesmo:
curl -X POST "http://localhost:8000/test/send-whatsapp" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "551198273281",
    "message": "Teste de mensagem",
    "test_id": "TEST_001"
  }'
```

---

## 📞 Suporte

Se encontrar problemas:

1. Verifique Cloud Logging
2. Procure a mensagem de erro
3. Verifique se todas as APIs estão habilitadas
4. Confirme credenciais do WhatsApp

Para mais detalhes, veja:
- `TESTING.md` - Testes da API
- `QUICK_TEST.md` - Testes rápidos
- Cloud Logging - Logs em tempo real
