# Piloto Automático — pesquisa, auditoria e decisões

## 1. O que o mercado faz (pesquisa de 22/11/2026)
Ferramentas analisadas: Lumi (alumi.ia), Ofertiva, Pro Afiliados, FluxoPromo, IA Divulgadora, Afilimais.

| Prática | Observado | O que fizemos |
|---|---|---|
| Fonte das ofertas | Radar de marketplaces, links colados, espelho de grupos de terceiros | Já temos: ofertas geradas pelo cliente (links de afiliado dele) + base do Cérebro |
| Texto | IA escreve gancho/mensagem; **não inventa preço, cupom, estoque nem urgência** (Ofertiva) | Compositor com variação (gancho, layout, emoji) e só dados da oferta |
| Destinos | Grupos e canais de WhatsApp, Telegram, cruzado | Grupo, canal (experimental) e Telegram |
| Anti-bloqueio | Intervalos aleatórios, janela de horário, limite diário, sem rajadas, sem repetir texto | Janela, intervalo ≥ 10 min, máx./dia, espaçamento aleatório entre destinos, texto variado, 1 envio por vez, expiração na fila |
| Conexão | Lumi e a maioria: o cliente lê QR code do próprio número | **Modelo diferente:** o cliente só adiciona o número/bot da ThyfIA como admin e confirma um código |
| Dono informado | Painel/histórico | Resumo diário e alertas no WhatsApp do cliente |

## 2. Limites técnicos (fontes oficiais)
- **API oficial do WhatsApp (Cloud API):** grupos só para Conta Comercial Oficial, no máximo 8 participantes, só grupos criados pela API; **canais não têm API de publicação**. Não serve para grupos de ofertas.
- **Telegram Bot API:** ~20 mensagens/min por grupo/canal, ~1/s por chat; 429 traz `retry_after` (tratado como "adiar").
- **Evolution/Baileys (não oficial):** é o que o mercado usa; viola os termos do WhatsApp e tem risco de bloqueio do número. Envio a canal (`@newsletter`) é recente e depende da versão. A partir da v2.4.0 a Evolution exige ativação em servidor de licença: **não atualizar a Evolution sem testar**.

## 3. Arquitetura do Piloto
```
ofertas do cliente ─► Cérebro.curar_ofertas ─► mensagens.compor ─► fila (piloto_envios)
   (job_itens)         pontua/explica            variação           ─► envio_canais (Evolution / Telegram)
                                                                    ─► relatório + alertas ao dono
```
- **Cérebro (`cerebro.curar_ofertas`)**: descarta o que a comunidade viu falhar; premia link já validado e busca com boa taxa; premia frescor e preço abaixo da mediana; diversifica categorias. Cada escolha vem com os motivos.
- **Fila**: `piloto_config` / `piloto_destinos` / `piloto_envios`; agendador próprio (20 s); um envio por chamada; falhas de conexão pausam 30 min; 3 falhas seguidas desligam o destino e avisam o dono.
- **WhatsApp do cliente por QR code (preferido)**: o cliente lê o QR no painel; a ThyfIA cria a instância dele na Evolution (`thyfia-u{id}`, `POST /instance/create` com `qrcode:true`, renovação do QR por `GET /instance/connect/{inst}`, estado por `GET /instance/connectionState/{inst}`, número por `GET /instance/fetchInstances?instanceName=`). Em seguida lista os grupos dele (`GET /group/fetchAllGroups/{inst}?getParticipants=false`) e ele marca onde o Piloto publica (`PUT /api/piloto/whatsapp/grupos` define a seleção exata). Grupos do próprio WhatsApp nascem confirmados (sem código). Cada instância tem o seu espaçamento anti-bloqueio. Se a conexão cair, o Piloto pausa, marca "desconectado" e avisa o dono (pelo número da ThyfIA) para ler o QR de novo; os grupos escolhidos ficam salvos.
- **Destinos com código (alternativa)**: para grupo/canal fora do WhatsApp do cliente, o número da ThyfIA envia um código de 4 dígitos ao destino; só quem lê o destino confirma. Evita apontar para grupo alheio.
- **Nichos** (`thyfia/nichos.py`): cada destino tem um nicho (`geral`, fit, tech, casa, auto, beleza, moda, pet, bebê, ferramentas). O Cérebro classifica cada oferta pelo termo da busca (peso 3) e pelo título (peso 1), com frases prontas valendo mais ("carregador veicular" → auto, "carregador" → tech). A cada ciclo, `planejar` escolhe a melhor oferta **de cada nicho que tem grupo** (a diversificação de categorias olha só o que saiu para aquele nicho) e a manda só aos grupos dele; grupos `geral` recebem a melhor das que sobraram. Nicho sem oferta é pulado e o dono é avisado (1x/dia, dizendo quais). O limite diário vale **por grupo**. Sem nichos configurados, tudo continua como antes (uma oferta para todos). **Na hora da busca** (Gerar links, planos com o Piloto): o campo "Para qual grupo?" já vem em "Automático (nicho que o Cérebro deduziu)" e deixa trocar; se o Cérebro não reconhece a busca, avisa e pede a escolha. A escolha vale só para aquela busca (digitar outro termo volta ao automático), é gravada como correção do cliente (`nicho_busca`) e ensina o Cérebro. `GET /api/nicho-sugerido?busca=` devolve a sugestão e `POST /api/jobs` aceita `nicho`. O painel mostra a cobertura por nicho (grupos × ofertas prontas) e o resumo do dia traz o estoque por nicho.
- **Plano**: Pro (recurso `automacao`).

## 3.1 O que o cartão da busca do Mercado Livre entrega (confirmado em 22/11/2026)
Lido pela extensão no JSON `"polycard"` da lista de resultados (`tests/fixtures/cartoes_ml_busca.txt` é uma amostra real).
Se o Mercado Livre mudar o formato, estes são os campos a conferir em `motor_busca.py`:

| Dado | Campo no JSON | Como a mensagem usa |
|---|---|---|
| Selo | `float_highlight_generic.highlight` → `{"id":"best_seller","text":"MAIS VENDIDO"}`; outro visto: `commercial_event` = "BLACK FRIDAY" | "🏆 Mais vendido no Mercado Livre" / "🖤 Black Friday". Selo desconhecido é guardado, mas não vai ao texto. O Cérebro dá +6 pontos ao "Mais vendido". |
| Preço "de" e % | `components[price].previous_price.value` e `discount_pct` | "~R$ de~ por *R$ preço* (X% OFF)". Só se o "de" for maior que o preço; % entre 5 e 90. Com o "de" do ML, a frase "Baixou X%" do nosso histórico não repete. |
| Condição do preço | `price.discount_complement.label.text`: "com Cupom" ou "no Pix" | O preço exibido **só vale** nessa condição. "com cupom" + "🎟️ Ative o cupom na página do produto antes de comprar"; "no Pix". Valor e código do cupom o cartão não traz. |
| Vendas e nota | `signal.sales`, `signal.reviews_rating` | "⭐ 4,8 · +250 mil vendidos", só com nota ≥ 4,5 e ≥ 500 vendas. |

Não aparecem na busca: "Mais compartilhado", "Mais buscado" e a comissão ("Ganhos %"); são do Hub de afiliados.
Cartões de anúncio (`is_advertising`, URL `click1.`) continuam descartados. O cartão "no Pix" ainda não foi visto em JSON
(só no HTML da página): o parser assume o mesmo campo `discount_complement` do "com Cupom".
Fora da lista de itens guardados e do texto: frete (depende do CEP de quem vê), "20% OFF com Saldo no Mercado Pago" (depende de pagamento) e loja oficial.

## 4. Auditoria do n8n (workflows 03 e 04)
1. **03 envia a si mesmo, não ao grupo**: manda a oferta para o seu número para você encaminhar; instância da Evolution fixa no nó (`Integradata tec`) e a coluna `Instancia` da planilha é ignorada.
2. **Planilha como fila**: sem trava (duas execuções podem pegar a mesma linha), sem retentativa, sem registro de erro por linha.
3. **Título/preço/foto por raspagem HTTP** da página do produto a cada envio: frágil (bloqueio, mudança de HTML) e lento; a ThyfIA já tem título, preço e foto.
4. **Token do webhook gravado em texto no nó "Token correto?" do 04**: deve ir para credencial (Header Auth) e ser trocado.
5. **04 teve 4 falhas em 21/11** (14:46–16:20), antes das correções; hoje roda.
6. Sem alerta quando o fluxo falha ou a fila acaba.
**Recomendação:** o Piloto passa a ser o caminho principal (envio, fila e curadoria dentro da ThyfIA). O n8n fica para orquestrações auxiliares (ex.: alerta de falha para o admin, rotinas por cliente, integrações novas). Os fluxos 03/04 seguem funcionando para o dono enquanto o Piloto é validado; nada neles foi alterado.

## 5. Ainda não verificado em produção (testar antes de vender)
- Resposta real de `group/inviteInfo` da sua versão da Evolution (parser tolerante, mas não testado ao vivo).
- Envio a `@newsletter` na sua versão.
- O número da ThyfIA precisa estar conectado na instância e ser admin dos grupos de teste.
- Formato exato das respostas de `instance/create`, `instance/connect` e `fetchAllGroups` na sua versão da Evolution: os leitores aceitam as variações documentadas (QR em `base64` ou `qrcode.base64`, número em `owner`/`ownerJid`), mas confirme ao vivo com um número de teste. Em v2.4+ a Evolution exige ativação de licença: não atualize às cegas.
- Risco de banimento do número do cliente (API não oficial, como a Lumi): o ritmo mínimo de 10 min e o espaçamento aleatório existem por isso; vale avisar o cliente no onboarding.

## 6. Próximas fases
- **Tratar mensagens** (responder "ainda vale?", remover links de terceiros): webhook de mensagens da Evolution + regras do Cérebro.
- **Pool de números** adicionais por cliente para diluir risco de bloqueio (a coluna `instancia` dos destinos já permite).
- **Nichos personalizados** e correção manual do nicho de uma oferta; hoje a classificação é por palavras-chave (termo novo sem pista cai em "sem nicho" e só vai a grupo geral). Evolução: aprender o nicho de termos novos pela vizinhança no Cérebro (`cerebro.relacionados`).
- **Reescrita por LLM** opcional (chave própria), mantendo a regra de não inventar dados.
- Reposição de estoque: hoje o Piloto avisa; gerar links segue dependendo do navegador do cliente.
