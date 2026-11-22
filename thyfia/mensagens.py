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


def prova_de_preco(item: dict) -> str:
    """Frase SÓ com o que o histórico de preços da ThyfIA comprova (nunca inventa desconto). Vazio se não houver."""
    pct, ref = item.get("queda_pct") or 0, item.get("preco_ref")
    if pct >= 5 and ref:
        return f"\n📉 Baixou {round(pct)}% (já esteve ~R$ {preco_br(ref)})"
    if item.get("menor_preco"):
        return "\n⭐ Menor preço que registramos"
    return ""


def compor(item: dict, estilo: str = "variado", semente: int = 0, variante: str | None = None) -> dict:
    """item: {titulo, preco, link_afiliado, busca}. Devolve {texto (WhatsApp), html (Telegram), titulo, preco, layout}.
    `variante` (A/B/C) força o layout que o Cérebro aprendeu que rende mais; vazio = varia sozinho."""
    rnd = random.Random(f"{item.get('id', 0)}-{semente}")
    if estilo == "animado":
        pool = GANCHOS_ANIMADO
    elif estilo == "direto":
        pool = GANCHOS_DIRETO
    else:
        pool = GANCHOS_ANIMADO + GANCHOS_DIRETO
    gancho = rnd.choice(pool)
    titulo = limpar_titulo(item.get("titulo", ""))
    preco = preco_br(item["preco"])
    emoji = emoji_da(item.get("busca", ""), titulo)
    link = (item.get("link_afiliado") or "").strip()
    layout = rnd.choice("ABC")
    if variante in ("A", "B", "C"):
        layout = variante
    prova = prova_de_preco(item)

    if layout == "A":
        wa = f"{gancho}\n\n{emoji} *{titulo}*\n\n💰 Por *R$ {preco}*{prova}\n\n_{AVISO}_\n👉 {link}"
    elif layout == "B":
        wa = f"{gancho}\n\n*{titulo}*\n💰 R$ {preco}{prova}\n\n_{AVISO}_\n🛒 Compre aqui: {link}"
    else:
        wa = f"{emoji} {gancho}\n*{titulo}*\n\nPor apenas *R$ {preco}*{prova} 👇\n{link}\n\n_{AVISO}_"

    esc = html.escape
    corpo = {"A": f"{esc(gancho)}\n\n{emoji} <b>{esc(titulo)}</b>\n\n💰 Por <b>R$ {preco}</b>{esc(prova)}",
             "B": f"{esc(gancho)}\n\n<b>{esc(titulo)}</b>\n💰 R$ {preco}{esc(prova)}",
             "C": f"{emoji} {esc(gancho)}\n<b>{esc(titulo)}</b>\n\nPor apenas <b>R$ {preco}</b>{esc(prova)} 👇"}[layout]
    return {"texto": wa, "html": f"{corpo}\n\n<i>{esc(AVISO)}</i>", "titulo": titulo, "preco": preco, "layout": layout}
