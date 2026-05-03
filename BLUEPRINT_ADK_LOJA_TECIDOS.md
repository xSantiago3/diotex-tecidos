# Blueprint ADK - Loja de Tecidos

## Objetivo do MVP

Construir um atendimento automatizado para site e WhatsApp usando Google ADK, com um orquestrador e agentes especializados para:

- responder duvidas sobre produtos;
- buscar e enviar fotos de produtos;
- calcular frete por CEP;
- gerar link de pagamento;
- receber comprovante e encaminhar o resumo do pedido aos socios;
- permitir atualizacao de preco e estoque apenas por numero autorizado com OTP.

Fase 1 cobre WooCommerce e WhatsApp Business API da Meta.
Shopee e Mercado Livre ficam para fase posterior.

## Stack recomendada

### Aplicacao

- Python 3.11+
- Google ADK para orquestracao e agentes
- FastAPI para webhooks e APIs internas
- Cloud Run para deploy do backend
- Cloud SQL for PostgreSQL como banco principal
- Cloud Storage para imagens auxiliares, exportacoes e arquivos de comprovante quando necessario
- Secret Manager para chaves e tokens

### Custo-beneficio

PostgreSQL em Cloud SQL e um bom meio termo porque:

- voce ja conhece SQL;
- facilita auditoria, relatorios e conciliacao de pedidos;
- modela bem catalogo, pedidos, estoque e logs administrativos;
- evita complexidade maior de um ecossistema NoSQL para esse caso.

## Canais e integracoes

### Site

- WooCommerce permanece como vitrine e origem inicial de catalogo
- plugin ou webhook envia produtos, precos e imagens para o backend ADK
- chatbot no site consome o mesmo backend do WhatsApp

### WhatsApp

- Meta WhatsApp Business API
- webhook de mensagens ligado ao backend
- mesma logica de negocio do site
- numero admin autorizado inicial: +5511982732814

### Frete

- Melhor Envio como opcao principal
- Correios como fallback, se necessario
- prazo informado ao cliente: prazo da transportadora + 2 dias de preparacao
- mensagem comercial pode informar que o envio pode ocorrer no mesmo dia, quando possivel

### Pagamento

- gerar link de pagamento ou cobranca Pix
- priorizar Pix para reduzir taxa
- registrar id da cobranca no pedido para conciliacao

### Notificacao aos socios

- e-mails: santiago-allan@hotmail.com, diotextecidos@gmail.com
- disparo ao receber comprovante ou pedido pronto para aprovacao manual

## Arquitetura de agentes

### 1. Orquestrador

Responsavel por:

- receber eventos de WhatsApp e site;
- detectar idioma;
- classificar intencao;
- encaminhar para o agente correto;
- aplicar guardrails e regras de negocio.

Intencoes principais:

- consulta de produto;
- recomendacao;
- envio de fotos;
- calculo de frete;
- montagem de pedido;
- pagamento;
- comprovante;
- status de pedido;
- comando administrativo.

### 2. Agente de Catalogo

Responsavel por:

- consultar produtos no banco sincronizado com WooCommerce;
- responder nome, composicao, largura, cor, preco e observacoes;
- localizar imagens por produto;
- enviar ate N imagens por resposta, conforme o canal permitir.

Regra:

- nunca altera descricao ou preco;
- apenas consulta e apresenta.

### 3. Agente Comercial

Responsavel por:

- sugerir tecidos por uso;
- comparar produtos semelhantes;
- orientar quantidade quando houver regra cadastrada;
- conduzir o cliente ate o fechamento.

Regra:

- sem desconto;
- sem prometer prazo menor que o permitido.

### 4. Agente de Checkout

Responsavel por:

- coletar itens e quantidade;
- pedir CEP;
- consultar frete;
- validar limite de peso e dimensoes;
- gerar pedido preliminar;
- gerar link de pagamento/Pix.

Regras:

- respeitar limite maximo retornado por Melhor Envio/Correios;
- se exceder limite, sugerir dividir pedido ou mudar modalidade.

### 5. Agente de Comprovante e Handoff

Responsavel por:

- identificar envio de comprovante;
- anexar comprovante ao pedido;
- enviar resumo do pedido e dados do cliente para os socios;
- marcar o pedido como aguardando conferencia humana.

Importante:

- o comprovante nao deve ser tratado como confirmacao financeira automatica sem conciliacao;
- o agente apenas organiza e encaminha.

### 6. Agente de Pos-venda

Responsavel por:

- informar status do pedido;
- orientar politica de envio;
- responder duvidas depois da compra.

### 7. Agente Admin

Responsavel por:

- permitir escrita em preco e estoque;
- executar somente quando origem for numero autorizado;
- exigir OTP antes de efetivar alteracoes;
- auditar tudo.

Regras de seguranca:

- whitelist inicial: +5511982732814
- enviar OTP de uso curto para confirmar operacao
- gravar autor, hora, IP/webhook id, antes/depois e motivo
- comandos aceitos devem ser estruturados

Exemplos:

- ATUALIZAR ESTOQUE produto=123 quantidade=18
- ATUALIZAR PRECO produto=123 preco=59.90

Fluxo:

1. socio envia comando;
2. sistema valida numero;
3. sistema gera OTP;
4. socio confirma OTP;
5. agente executa a alteracao;
6. sistema grava auditoria e retorna comprovante da mudanca.

## Regras de negocio consolidadas

- idioma padrao: portugues brasileiro
- trocar para espanhol ou ingles quando solicitado ou detectado
- agente publico nunca altera preco, descricao ou regras comerciais
- agente admin pode alterar preco e estoque somente com numero autorizado + OTP
- sem descontos automaticos
- prazo ao cliente: transportadora + 2 dias de preparacao
- mensagem pode informar que em alguns casos o envio ocorre no mesmo dia
- frete deve respeitar limite de peso e dimensoes da integracao usada

## Modelo de dados inicial

### Tabela products

- id
- woo_product_id
- name
- slug
- description
- price
- currency
- width_cm
- composition
- color
- unit_type
- active
- created_at
- updated_at

### Tabela product_images

- id
- product_id
- source_url
- file_name
- sort_order
- created_at

### Tabela inventory

- id
- product_id
- available_quantity
- reserved_quantity
- updated_at
- updated_by

### Tabela orders

- id
- channel
- customer_name
- customer_phone
- customer_email
- shipping_zipcode
- shipping_quote_json
- subtotal_amount
- shipping_amount
- total_amount
- payment_provider
- payment_reference
- status
- created_at
- updated_at

### Tabela order_items

- id
- order_id
- product_id
- product_name_snapshot
- unit_price_snapshot
- quantity
- line_total

### Tabela admin_actions

- id
- actor_phone
- action_type
- target_table
- target_id
- payload_before_json
- payload_after_json
- otp_verified_at
- created_at

## Sincronizacao com WooCommerce

### Estrategia inicial

1. exportar catalogo do WooCommerce;
2. importar no banco principal;
3. criar sincronizacao recorrente por API/webhook;
4. manter WooCommerce como origem inicial de cadastro publico;
5. permitir que ajustes de preco/estoque sejam feitos no backend admin e opcionalmente retornem ao WooCommerce.

### Observacao importante

Como hoje nao ha controle de estoque, vale separar:

- catalogo publico vindo do WooCommerce;
- estoque operacional mantido no banco do backend.

Assim, o agente consegue operar antes de um ERP completo existir.

## Imagens e identificacao sem SKU

Situacao atual: imagens com nomes de arquivo, sem SKU padronizado.

Solucao de MVP:

1. importar lista de produtos do WooCommerce;
2. importar imagens disponiveis;
3. rodar correspondencia por similaridade entre nome do arquivo e nome do produto;
4. revisar manualmente os casos ambiguos em uma fila;
5. salvar vinculacao final em product_images.

Regra pratica:

- nao depender de IA generativa para vincular tudo automaticamente sem revisao;
- usar heuristica + aprovacao humana nos conflitos.

## Gateways de pagamento: opcoes e tradeoffs

### 1. Efí (antigo Gerencianet)

Bom para:

- Pix;
- boleto;
- custo geralmente competitivo;
- bastante usado em operacoes brasileiras.

Ponto forte:

- costuma fazer sentido quando o foco e reduzir taxa com Pix.

### 2. Asaas

Bom para:

- cobranca simples;
- Pix e boleto;
- operacao facil para times pequenos;
- boa experiencia para links de pagamento.

Ponto forte:

- simples de operar e integrar.

### 3. Pagar.me

Bom para:

- operacao mais robusta;
- cartao, Pix e recorrencia;
- mais flexibilidade para crescer.

Ponto forte:

- stack madura para e-commerce e conciliacao.

### 4. Mercado Pago

Bom para:

- implantacao rapida;
- links de pagamento conhecidos do publico;
- suporte a Pix e cartao.

Ponto forte:

- onboarding simples.

Ponto de atencao:

- taxa pode nao ser a menor opçao dependendo do arranjo comercial.

### Recomendacao inicial

Se a prioridade for menor taxa e simplicidade no Brasil, comece avaliando:

1. Efí
2. Asaas
3. Pagar.me

Para esse projeto, eu evitaria escolher pelo nome mais conhecido e compararia:

- taxa de Pix;
- taxa do link de pagamento;
- prazo de recebimento;
- facilidade de webhook;
- custo de saque/transferencia;
- suporte a split, se um dia precisarem.

## Fluxo operacional do MVP

### Fluxo de venda no WhatsApp

1. cliente pergunta sobre um tecido;
2. orquestrador chama catalogo/comercial;
3. agente responde em portugues, espanhol ou ingles conforme a conversa;
4. cliente pede fotos;
5. agente envia imagens vinculadas ao produto;
6. cliente escolhe item e quantidade;
7. checkout solicita CEP;
8. sistema consulta frete;
9. agente informa prazo da transportadora + 2 dias de preparacao;
10. sistema gera link de pagamento/Pix;
11. cliente envia comprovante;
12. agente envia resumo aos socios por e-mail;
13. pedido fica aguardando conferencia humana.

### Fluxo admin no WhatsApp

1. socio envia comando estruturado;
2. sistema confere whitelist;
3. sistema envia OTP;
4. socio confirma OTP;
5. agente atualiza preco ou estoque no banco;
6. sistema grava auditoria.

## Backlog de implementacao

### Sprint 1

- criar backend FastAPI
- configurar webhooks do WhatsApp
- modelar banco PostgreSQL
- importar catalogo do WooCommerce
- criar orquestrador ADK

### Sprint 2

- agente de catalogo
- agente comercial
- vinculo inicial de imagens
- chatbot do site

### Sprint 3

- integracao Melhor Envio/Correios
- agente de checkout
- integracao com gateway de pagamento
- status de pedido

### Sprint 4

- agente de comprovante e handoff
- envio de e-mail para socios
- agente admin com OTP
- trilha de auditoria

## Decisoes ja fechadas

- canal prioritario: site + WhatsApp
- origem publica inicial: WooCommerce
- banco principal: PostgreSQL
- autenticacao admin: numero autorizado + OTP
- prazo extra operacional: 2 dias
- idioma principal: portugues com troca dinamica para espanhol/ingles

## Proximos passos recomendados

1. escolher o gateway de pagamento inicial
2. listar campos reais de produto existentes no WooCommerce
3. separar um lote de imagens para a primeira vinculacao
4. definir se o OTP vai por WhatsApp, e-mail ou app autenticador
5. iniciar scaffold do backend ADK + FastAPI