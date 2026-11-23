# Shopee na ThyfIA

## Como funciona (e por que é diferente do Mercado Livre)

| | Mercado Livre | Shopee |
|---|---|---|
| Busca de produtos | no navegador da pessoa (extensão) | API oficial, no servidor |
| Geração do link | gerador do ML, no navegador | `generateShortLink`, no servidor |
| Login | sessão do navegador | App ID + Secret da pessoa |
| Captcha / bloqueio de IP | sim | não |

A Shopee oferece a **Open API de Afiliados** (GraphQL). Por isso a busca e a geração dos links são 100% automáticas:
sem extensão, sem cookie, sem navegador aberto. O que o usuário precisa fazer, uma vez só, é ter a conta aprovada no
programa de afiliados e copiar o **App ID** e o **Secret** para a ThyfIA.

## O que o usuário faz

1. Conta aprovada em affiliate.shopee.com.br.
2. Portal do Afiliado > menu **Open API** > solicitar acesso (se ainda não tiver) > copiar App ID e Secret.
3. ThyfIA > Integrações > cartão da Shopee > colar > **Conectar Shopee**.
4. Gerar links > escolher **Shopee** > palavra-chave > Gerar.

A conexão só é gravada depois de uma consulta de verdade à Shopee. O Secret fica cifrado (`cofre`) e nunca volta ao navegador.
A comissão vai para a conta do próprio usuário. Se a conta ainda não tem acesso à Open API, a ThyfIA mostra a mensagem
(erro 10035) explicando onde pedir.

## O que a ThyfIA faz por baixo

- `thyfia/shopee.py`: cliente assinado (`Authorization: SHA256 Credential=…, Timestamp=…, Signature=sha256(AppId+ts+corpo+Secret)`),
  repete com espera em 10030 (limite) e 10000 (instabilidade), traduz os códigos de erro.
- Busca: `productOfferV2(keyword, sortType: relevância, limit)` pede o dobro do necessário e ranqueia aqui
  (vendas e nota pesam mais; a comissão dá até +15%); tira repetidos e itens fora do assunto.
- Links: `generateShortLink` em lotes de 10, com `subIds: ["thyfia", "<busca>"]` (aparecem no relatório de conversões
  da Shopee). Se a geração falhar, vale o `offerLink` que já veio da busca (também é link de afiliado).
- Job: `jobs.fonte = 'shopee'`; roda em thread própria (`shopee.executar_job`) e reaproveita planilha, histórico,
  envio automático, Cérebro, Piloto e Agente de Publicação (hashtag `#shopee` no lugar de `#mercadolivre`).
- Cérebro: o aprendizado guarda a loja pelo endereço; a recuperação do Mercado Livre filtra `mercadolivre.com`
  para não sugerir produto da Shopee.
- Rotas: `GET /api/shopee/status`, `POST /api/shopee/conectar`, `POST /api/shopee/desconectar`,
  `POST /api/shopee/link` (o "Link personalizado" do Portal: cola um produto/loja, recebe o link curto),
  `POST /api/jobs` com `fonte: "shopee"`.

## Pontos a confirmar com credenciais reais

As documentações públicas da API são de terceiros (a oficial exige login). Está tudo implementado de forma tolerante,
mas vale conferir no primeiro teste:

- se `commissionRate` vem em fração (0,12) ou percentual (12): a leitura aceita os dois;
- formato da `imageUrl` (o Instagram exige JPEG; o hospedeiro de imagens converte, mas confirme com um post);
- se a consulta aceita todos os campos pedidos; se a Shopee recusar (11001/10010), a busca refaz com o conjunto básico;
- limite de consultas por minuto (não documentado).

## Ideias para depois

- preço "de/por" derivado de `priceDiscountRate`, "a partir de" para faixas de preço e comissão estimada na tela;
- importação manual (colar vários links) para quem ainda não tem acesso à Open API;
- relatório de conversões por `subId` dentro da ThyfIA.
