# Cérebro — como a ThyfIA aprende (sem LLM)

## 1. Diagnóstico do que existia (antes deste PR)

| Parte | Como funcionava | Limite |
|---|---|---|
| Aprendizado | Depois de cada busca grava `termos` (buscas, links ok/total) e `produtos` (vistos, gerados, falhas) | O único sinal era técnico: "o link foi gerado?". Nunca soube se alguém **clicou**, **gostou** ou **comprou** |
| Recuperação | BM25 em Python sobre `titulo_norm`, pré-filtrado por `LIKE '%palavra%'` | Sem plural/sinônimo, varre a tabela inteira por busca |
| Preço | `produtos.preco` guarda só o último valor, como texto | Sem histórico: impossível afirmar "caiu" com verdade |
| Curadoria (Piloto) | Pesos fixos escritos à mão (frescor, taxa da busca, mediana) | Não se adapta ao público de cada cliente |
| Nicho | Lista de palavras-chave fixa | Termo novo ficava "sem nicho" para sempre |
| Mensagem | Layout A/B/C sorteado | Sem aprender qual rende mais |
| Dono | Aprovar/pular uma oferta no modo "eu aprovo" | Era descartado, nunca virava aprendizado |

## 2. O que foi construído (`thyfia/aprendizado.py`)

O que um assistente com memória faz — **lembrar, relacionar, aprender com o retorno e entregar de forma personalizada** — feito com estatística simples, explicável e rodando no próprio servidor.

1. **Memória de preços.** `preco_historico` (1 observação/produto/dia, dado público), alimentada a cada busca e reconstruída dos itens já gerados na primeira execução. Queda = preço atual ≥ 5 % abaixo da **mediana dos dias anteriores** (mínimo 2 dias de histórico); "menor preço" exige 3 dias. A curadoria premia a queda e a mensagem só diz "📉 Baixou X %" ou "⭐ Menor preço que registramos" quando o histórico comprova — nunca inventa desconto.
2. **Classificador de nicho que aprende.** Naive Bayes multinomial (Laplace 0,5) semeado com as palavras-chave do catálogo. Auto-treino **só com pista confiante** (uma palavra-chave identifica o nicho com folga ≥ 3 sobre o segundo): as outras palavras do título/termo passam a indicar aquele nicho ("creatina monohidratada" ⇒ "monohidratada" → fit). Ordem de decisão: correção do cliente → palavra-chave → modelo (só afirma com probabilidade ≥ 0,75). Medidas ("900g", "65w") são ignoradas.
3. **Feedback → bandit por cliente** (`cerebro_bandit`). Dimensões: nicho, busca, faixa de preço, layout da mensagem. Cada envio vira um sucesso/falha:
   * **clique medido** (peso 1): link da mensagem passa por `/r/{token}` e redireciona ao link de afiliado. Só conta gente de verdade (robôs de pré-visualização do WhatsApp/Telegram são descartados; 1 clique por visitante por envio). Avaliado após 24 h de maturação.
   * **aprovar/pular** no modo "eu aprovo" (peso 0,5).
   * **correção de nicho** pelo cliente (ensina o modelo com peso 2).
   O bônus na curadoria usa média encolhida (`ENCOLHE = 4`) contra a média do próprio público, com teto por dimensão (nicho ±8, busca ±12, faixa ±6) e **bônus de exploração que decai com a amostra** (busca ainda sem retorno ganha até +4 pts) para não prender o Piloto em poucos assuntos. Cada bônus entra como frase em português nos motivos ("seu público responde melhor a Fit e saúde (75 % contra 44 % da média)").
   * **Partida a frio segura:** sem evidência suficiente na dimensão (`MIN_TOTAL = 3`) o bandit não muda nada.
4. **Entrega.** O compositor passa a receber a variante (layout) que mais rende para aquele público; 35 % das mensagens continuam livres para manter a variedade (proteção contra detecção de spam).

Tudo é determinístico (sem sorteio escondido no ranking), então os testes e o "porquê" mostrado no painel são reproduzíveis.

## 3. Onde aparece
* **Cérebro → "O que o Cérebro aprendeu":** fase (sem dados / aprendendo / calibrado), retorno por nicho/busca/faixa/layout, palavras novas aprendidas por nicho, quedas de preço detectadas. API: `GET /api/cerebro/aprendizado`.
* **Piloto Automático:** interruptor **Medir cliques**, nicho de cada busca editável (corrige e ensina), cliques por envio, motivos da curadoria com o que foi aprendido.

## 4. O que isto NÃO é (para alinhar expectativa)
Não generaliza linguagem como um LLM: não entende frases novas nem escreve texto livre. Aprende **padrões estatísticos dos dados da própria plataforma** — o que cada nicho "soa" e o que cada público clica — e isso é exatamente o que dá retorno na divulgação de ofertas. Reescrita de mensagem por LLM continua opcional e futura.

## 5. Limites e como validar
* **Aprendizagem por clique depende de "Medir cliques" ligado** (opt-in; precisa do endereço público configurado no admin). Sem ele, só aprovar/pular alimenta o bandit.
* O link da mensagem passa a ser o do domínio da ThyfIA (redireciona direto ao de afiliado, sem página intermediária). Teste no celular: o WhatsApp/Telegram devem mostrar a pré-visualização do produto e o clique deve cair no produto.
* Clique ≠ compra. Se o Mercado Livre expuser relatório de conversões por link, ele é o próximo sinal (recompensa mais forte).
* Em produção, depois de alguns dias: conferir no painel se a fase passa de "aprendendo" e se as quedas de preço batem com o que se vê no ML.

## 6. Próximos passos naturais
* Recuperação com índice invertido/FTS5 + expansão de consulta por coocorrência (hoje `LIKE` + BM25 em Python).
* Melhor horário por público (dimensão `hora`) e aprendizado do ritmo.
* Aprender gancho/estilo (hoje só o layout).
