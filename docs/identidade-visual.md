# Identidade visual da ThyfIA no front

O front segue o **Design System ThyfIA** (tema Noite). Esta página diz onde cada coisa vive e as regras que o
`tests/test_frontend_estatico.py` protege.

## Onde está cada peça

| O quê | Onde |
| --- | --- |
| Tokens (cores, sombras de adesivo, espaçamento, raios, famílias) e `@font-face` | `thyfia/static/tokens.css` — gerado de `tokens.json` do design system; o tema **Claro** (`data-theme="claro"`) já vem junto, ainda sem uso na interface |
| Fontes (Fredoka, DM Sans, JetBrains Mono, woff2 locais) | `thyfia/static/fonts/` — sem Google Fonts |
| Logo, ícone do app, cabeça e as 6 poses do Thyf | `thyfia/static/marca/` (favicon e `apple-touch-icon.png` incluídos) |
| Estilo dos componentes | `thyfia/static/styles.css` — usa os tokens; os nomes antigos (`--roxo`, `--texto`…) são apelidos dos tokens novos |
| Ícones de interface (traço 2px, pontas redondas) | sprite `<symbol id="i-…">` no fim do `index.html`; no JS use `ic("nome")` |

## Regras

- **Cores chapadas.** Nada de degradê roxo-ciano nem fundo animado. Roxo `brand` é a ação principal (texto `on-brand`);
  ciano `accent` é o toque de IA.
- **Estilo adesivo** em tudo que se toca: borda de 2px em `edge` + sombra seca (`sticker-sm`/`sticker-md`); pressionado desce 2px.
  Foco de teclado: anel de 3px na cor `focus`.
- **Fredoka só em títulos de 20px ou mais**; texto corrido em DM Sans; links curtos e valores em JetBrains Mono. CAIXA ALTA só em selos.
- **Sem emoji na interface.** Ícones vêm do sprite. Emoji só dentro do texto das mensagens enviadas e das legendas de rede social.
- **Mercado Livre, Instagram, Facebook e WhatsApp aparecem como texto/ícone neutro**, sem logotipos nem cores de terceiros.
- **Um Thyf por tela:** `neutro` (estado vazio da busca), `pensando` (gerando links), `feliz` (links prontos, conta conectada, até logo),
  `dormindo` (Histórico vazio e Piloto Automático ligado).
- Sucesso e erro sempre levam **ícone ou palavra** (toasts, selos, histórico), nunca só a cor.

## Para adicionar um ícone

1. Inclua um `<symbol id="i-nome" viewBox="0 0 24 24">` no sprite (só `path`/`circle`/`rect`, sem `fill`/`stroke` — o CSS `.ic` define o traço).
2. Use `ic("nome")` no JS ou `<svg class="ic"><use href="#i-nome"/></svg>` no HTML.
3. O teste `test_icones_usados_existem_no_sprite` avisa se algum nome ficar sem `<symbol>`.
