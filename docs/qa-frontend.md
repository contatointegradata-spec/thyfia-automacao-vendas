# QA do frontend

Objetivo: nenhum texto sobreposto, cortado ou vazando para fora do bloco, em nenhum tamanho de tela.

## O que foi encontrado e corrigido (revisão de 22/11)

| # | Problema | Onde aparecia | Causa e correção |
|---|----------|---------------|------------------|
| 1 | Preço, lista e botão vazando do card | Meu plano (produção) | A classe `.pl-grade` existia em duas telas (planos e regras do Piloto). A do Piloto sobrescrevia o grid dos cards, que ficavam com 170 px. Renomeada para `.pl-regras`; a classe `.pl-aviso` também colidia (agora `.pl-alerta`). Abaixo de 1180 px os planos viram uma coluna. |
| 2 | Aviso vermelho "Faça login para continuar." ao clicar em Entrar / Criar conta | Tela de login (produção) | O clique global do admin respondia a qualquer `[data-ativo]`, e as abas do login usavam esse atributo. O clique agora só vale dentro de `#view-admin` e as abas usam `data-aba-ativa`. |
| 3 | Botão Pausar/Ligar de um destino também chamava a API de admin | Piloto (produção) | Mesmo atributo `data-ativo`. Renomeado para `data-liga`. |
| 4 | Lista "Buscas populares" translúcida, com os cards de baixo aparecendo por cima | Gerar links (produção) | A caixa de busca ficava num contexto de empilhamento abaixo dos cards seguintes. Fundo opaco e `z-index` próprio. |
| 5 | Rodapé da sidebar (plano, usuário, Sair) cortado | Janelas com menos de ~800 px de altura (produção) | Sidebar compacta em telas baixas e rodapé fixo (`sticky`). |
| 6 | Painel "O que o Cérebro aprendeu" aparecendo em todas as telas | Depois de visitar o Cérebro | O painel estava fora de `#view-cerebro`. |
| 7 | Formulário "Onde divulgar" estourando a tela do celular | Piloto, 390 px | Selects com largura mínima fixa; `.au-corpo` agora usa `minmax(0, 1fr)`. |
| 8 | Notificação com palavra longa estourando para fora da tela | Qualquer tela | `overflow-wrap` e largura máxima nos avisos. |
| 9 | "Salvar regras" colado nas caixas de seleção; painéis sem espaço entre si | Piloto | Espaçamento no formulário e entre painéis. |
| 10 | Botão "＋ Liberar plano" fora da tela; rótulos do Cérebro quebrando | Admin e Cérebro | Quebra de linha permitida e coluna de rótulo mais larga. |

## Como repetir a varredura

```bash
pip install playwright && playwright install chromium
tools/qa/executar.sh            # 1210, 1440, 1024, 768 e 390 px
tools/qa/executar.sh 390        # só um tamanho
```

O robô sobe a ThyfIA com dados de teste (`tools/qa/servidor_qa.py`: Evolution falsa, ofertas com títulos enormes,
conta Premium e admin) e passa por todas as telas e estados (login, cada menu, abas do admin, sugestões da busca,
modais, avisos, QR code, lista de grupos). Para cada tela ele aponta texto vazando do bloco pai, texto cortado,
texto coberto por outro elemento e sobreposição, e salva capturas em `qa_capturas/`.
Use `QA_PLANO=plus` ou `QA_ADMIN=0` para ver o menu como um cliente sem o plano.

Itens que o robô lista mas que são esperados (revise só se a tela mudar): o aviso "Conecte seu Mercado Livre"
cobre o formulário de propósito; a tela de login tem uma esteira com os dois formulários lado a lado; rótulos
flutuantes ficam "atrás" do campo; o dropdown de sugestões cobre os cards abaixo enquanto está aberto.

## Guardas automáticas (rodam no CI)

`tests/test_frontend_estatico.py` barra, sem navegador, os três erros que já chegaram a produção: classe CSS
definida duas vezes, painel fora de uma `.view` e uso de `data-ativo` fora do admin.

## Regras para novas telas

- Prefixe as classes com o nome da tela e confira se o prefixo já não é usado por outra (`pl-` serve a Plano e a Piloto).
- Não reutilize atributos `data-*` que tenham manipulador global (`data-ativo`, `data-ir`, `data-conceder`...). Escopo o manipulador na tela.
- Texto que vem de fora (título do Mercado Livre, nome de grupo, mensagem de erro) precisa de `overflow-wrap: anywhere`
  e de `min-width: 0` no contêiner flex/grid.
- Rode `tools/qa/executar.sh` antes de abrir o PR.
