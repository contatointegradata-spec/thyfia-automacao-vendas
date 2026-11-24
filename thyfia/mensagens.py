"""
Compositor de mensagens de oferta — o "texto" do Piloto Automático.

Sem LLM: gera variações de verdade (gancho, layout e emoji mudam por oferta e por destino) para o
texto não virar uma impressão digital repetida em vários grupos — isso derruba ofertas boas e
aumenta o risco de bloqueio. Regra de ouro (igual às melhores ferramentas do mercado): a IA nunca
inventa preço, cupom, estoque nem urgência; tudo vem da oferta.
"""

from __future__ import annotations

import html
import random
import re
import unicodedata

ESTILOS = ("variado", "animado", "direto")
AVISO = "Valor visto na busca; pode variar na loja."

GANCHOS_ANIMADO = (
    "🔥 Achadinho do dia!", "😱 Olha esse preço!", "⚡ Corre que vale a pena!",
    "🛒 Olha o que eu achei!", "💥 Oferta boa chegando!", "😍 Gostei muito desse!",
    "🎯 Esse aqui vale conferir!",
)
GANCHOS_DIRETO = ("📦 Oferta:", "💸 Preço bom:", "✅ Vale conferir:", "🏷️ Achado:", "👀 Olha só:")

_EMOJIS = (
    (("celular", "smartphone", "iphone", "galaxy", "xiaomi", "redmi"), "📱"),
    (("fone", "headset", "headphone", "airpods", "caixa de som", "soundbar"), "🎧"),
    (("tv", "monitor", "smart tv"), "📺"),
    (("notebook", "laptop", "teclado", "mouse", "ssd", "pc "), "💻"),
    (("tenis", "sapato", "sandalia", "chinelo", "bota"), "👟"),
    (("fritadeira", "airfryer", "cafeteira", "panela", "liquidificador", "batedeira"), "🍳"),
    (("perfume", "creme", "shampoo", "hidratante", "maquiagem"), "🌸"),
    (("relogio", "smartwatch"), "⌚"),
    (("bicicleta", "bike"), "🚲"),
    (("furadeira", "parafusadeira", "ferramenta", "chave"), "🔧"),
    (("brinquedo", "boneca", "lego"), "🧸"),
    (("mochila", "bolsa", "mala"), "🎒"),
    (("camera", "gopro", "tripe"), "📷"),
    (("whey", "creatina", "suplemento"), "💪"),
)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def preco_br(valor: float) -> str:
    return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def limpar_titulo(titulo: str, limite: int = 110) -> str:
    t = re.sub(r"\s+", " ", titulo or "").strip(" -–|·")
    letras = [c for c in t if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.7:  # GRITARIA → Título Normal
        t = t.title()
    if len(t) > limite:
        t = t[:limite].rsplit(" ", 1)[0].rstrip(" ,.-–|") + "…"
    return t


def emoji_da(busca: str, titulo: str) -> str:
    base = _norm(f"{busca} {titulo}")
    for chaves, emoji in _EMOJIS:
        if any(k in base for k in chaves):
            return emoji
    return "🛍️"


# Só entra na mensagem o que o cartão do Mercado Livre mostrou (selo, preço "de", cupom/Pix, vendas e nota).
SELOS = (("mais vendido", "🏆 Mais vendido no Mercado Livre"), ("black friday", "🖤 Black Friday"))
CONDICOES = {"cupom": " com cupom", "pix": " no Pix"}
AVISO_CUPOM = "🎟️ Ative o cupom na página do produto antes de comprar"
NOTA_MIN, VENDAS_MIN = 4.5, 500  # prova social só quando é boa notícia de verdade


def selo_linha(item: dict) -> str:
    t = _norm(item.get("selo") or "")
    return next((frase for chave, frase in SELOS if chave in t), "")


def preco_de(item: dict) -> float | None:
    """Preço "de" que o Mercado Livre mostra riscado, se for mesmo maior que o atual."""
    try:
        de, atual = float(item.get("preco_de") or 0), float(item["preco"])
    except (TypeError, ValueError):
        return None
    return de if de > atual * 1.03 else None


def vendas_txt(v) -> str:
    v = int(v or 0)
    if v < 500:
        return ""
    return f"+{v // 1000} mil" if v >= 5000 else f"+{v}"


def prova_social(item: dict) -> str:
    try:
        nota, vendas = float(item.get("nota") or 0), int(item.get("vendas") or 0)
    except (TypeError, ValueError):
        return ""
    if nota < NOTA_MIN or vendas < VENDAS_MIN:
        return ""
    return f"⭐ {nota:.1f} · {vendas_txt(vendas)} vendidos".replace(".", ",", 1)


def prova_de_preco(item: dict, sem_baixa: bool = False) -> str:
    """Frase SÓ com o que o histórico de preços da ThyfIA comprova (nunca inventa desconto). Vazio se não houver.
    `sem_baixa`: a mensagem já mostra o "de/por" do Mercado Livre; então não repete a queda."""
    pct, ref = item.get("queda_pct") or 0, item.get("preco_ref")
    if pct >= 5 and ref and not sem_baixa:
        return f"\n📉 Baixou {round(pct)}% (já esteve ~R$ {preco_br(ref)})"
    if item.get("menor_preco"):
        return "\n⭐ Menor preço que registramos"
    return ""


def destaque(item: dict, de: float | None, pct, selo: str, cond: str) -> str:
    """Linha de destaque com o fato mais forte DA OFERTA (nunca inventa): selo + desconto, ou cupom, ou Pix."""
    partes = []
    if selo:
        partes.append(selo.replace("Mais vendido no Mercado Livre", "*Mais vendido*", 1) if "Mais vendido" in selo else selo)
    if de and pct and 15 <= int(pct) <= 90:
        partes.append(f"🔥 *{int(pct)}% OFF*")
    if not partes and cond:
        partes.append("🎟️ *Preço com cupom*" if "cupom" in cond else "💳 *Preço no Pix*")
    return " · ".join(partes)


def gancho_da_oferta(item: dict, de: float | None, pct, selo: str, cond: str, estilo: str, rnd: random.Random) -> str:
    """Gancho que combina com o que a oferta REALMENTE tem; sem fato forte, cai no gancho geral do estilo."""
    if de and pct and int(pct) >= 30:
        return rnd.choice((f"🔥 {int(pct)}% OFF — corre!", "😱 Olha esse desconto!", f"💥 Caiu {int(pct)}%!"))
    if "cupom" in cond:
        return rnd.choice(("🎟️ Esse aqui tem cupom!", "🎟️ Ative o cupom e economize!"))
    if selo:
        return rnd.choice(("🏆 Está entre os mais vendidos!", "🏆 Queridinho do Mercado Livre!"))
    if (item.get("queda_pct") or 0) >= 5:
        return rnd.choice(("📉 O preço caiu!", "📉 Baixou de preço!"))
    pool = GANCHOS_ANIMADO if estilo == "animado" else GANCHOS_DIRETO if estilo == "direto" else GANCHOS_ANIMADO + GANCHOS_DIRETO
    return rnd.choice(pool)


def compor(item: dict, estilo: str = "variado", semente: int = 0, variante: str | None = None) -> dict:
    """item: {titulo, preco, link_afiliado, busca}. Devolve {texto (WhatsApp), html (Telegram), titulo, preco, layout}.

    Todos os layouts seguem o modelo dos grupos de ofertas (título, "De" riscado, "Por" em negrito, avisos, link).
    O que muda é a ABERTURA, e é isso que o Cérebro testa e aprende (qual rende mais cliques para o público):
      A: modelo seco, sem abertura.
      B: emoji do produto + linha de destaque com o fato mais forte da oferta (selo, % OFF, cupom, Pix).
      C: gancho de abertura que combina com a oferta (desconto, cupom, mais vendido, queda) e emoji no título.
    `variante` (A/B/C) força o layout que o Cérebro aprendeu que rende mais; vazio = varia sozinho."""
    rnd = random.Random(f"{item.get('id', 0)}-{semente}")
    titulo = limpar_titulo(item.get("titulo", ""))
    preco = preco_br(item["preco"])
    emoji = emoji_da(item.get("busca", ""), titulo)
    link = (item.get("link_afiliado") or "").strip()
    layout = variante if variante in ("A", "B", "C") else rnd.choice("ABC")
    de = preco_de(item)
    de_txt = preco_br(de) if de else ""
    prova = prova_de_preco(item, sem_baixa=bool(de))
    cond = CONDICOES.get(item.get("condicao") or "", "")
    pct = item.get("desconto_pct")
    pct_ok = bool(de and pct and 5 <= int(pct) <= 90)
    selo = selo_linha(item)
    extras = [x for x in (prova_social(item), AVISO_CUPOM if item.get("condicao") == "cupom" else "") if x]

    topo_txt = destaque(item, de, pct, selo, cond) if layout == "B" else ""
    gancho = gancho_da_oferta(item, de, pct, selo, cond, estilo, rnd) if layout == "C" else ""
    sel_embaixo = selo if layout != "B" else ""  # no B o selo sobe para a linha de destaque
    pct_txt = f" ({int(pct)}% OFF)" if pct_ok and (layout == "A" or (layout == "C" and "%" not in gancho)) else ""
    com_emoji = layout in ("B", "C")

    # Modelo:  [abertura]  *Título*  [selo]  /  De ~R$ 839,00~  /  Por *R$ 433,00* com cupom (48% OFF)  /  link
    cabeca = (f"{gancho}\n\n" if gancho else "") + (f"{topo_txt}\n" if topo_txt else "") + (f"{emoji} " if com_emoji else "")
    topo = f"\n{sel_embaixo}" if sel_embaixo else ""
    preco_wa = (f"De ~R$ {de_txt}~\n" if de else "") + f"Por *R$ {preco}*" + cond + pct_txt
    avisos = "".join(f"\n{x}" for x in extras)
    wa = f"{cabeca}*{titulo}*{topo}\n\n{preco_wa}{prova}{avisos}\n\n{link}"

    esc = html.escape
    to_html = lambda t: re.sub(r"\*([^*]+)\*", r"<b>\1</b>", esc(t))  # destaque em negrito do WhatsApp -> HTML do Telegram
    cabeca_h = (f"{esc(gancho)}\n\n" if gancho else "") + (f"{to_html(topo_txt)}\n" if topo_txt else "") + (f"{emoji} " if com_emoji else "")
    topo_h = f"\n{esc(sel_embaixo)}" if sel_embaixo else ""
    preco_h = (f"De <s>R$ {de_txt}</s>\n" if de else "") + f"Por <b>R$ {preco}</b>" + esc(cond + pct_txt)
    avisos_h = "".join(f"\n{esc(x)}" for x in extras)
    corpo = f"{cabeca_h}<b>{esc(titulo)}</b>{topo_h}\n\n{preco_h}{esc(prova)}{avisos_h}\n\n{esc(link)}"
    return {"texto": wa, "html": corpo, "titulo": titulo, "preco": preco, "layout": layout}
