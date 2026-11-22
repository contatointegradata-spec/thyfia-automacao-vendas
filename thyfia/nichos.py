"""
Nichos do Piloto Automático: o Cérebro descobre a que nicho cada oferta pertence e o Piloto a manda só para os
grupos daquele nicho (um mesmo WhatsApp pode ter 2 grupos fit, 1 de tech, 1 de casa, 1 de automóvel…).

A classificação olha o termo de busca (peso 3 — foi o cliente quem escolheu) e o título do produto (peso 1).
Sem nenhuma pista, a oferta fica sem nicho e só vai para grupos "Todos os nichos".
"""

from __future__ import annotations

import re

from . import cerebro

GERAL = "geral"

# slug → (nome, emoji, palavras-chave no singular e sem acento). Frases valem como uma pista só, mais forte.
NICHOS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    GERAL: ("Todos os nichos", "🌐", ()),
    "fit": ("Fit e saúde", "💪", (
        "whey", "creatina", "suplemento", "proteina", "academia", "treino", "treinar", "halter", "haltere", "anilha",
        "kettlebell", "yoga", "pilates", "colchonete", "elastico", "musculacao", "bcaa", "glutamina", "termogenico",
        "coqueteleira", "shaker", "esteira", "ergometrica", "corrida", "fitness", "fit", "caneleira", "pre treino",
        "corda de pular", "barra fixa", "luva de treino", "cinto de musculacao", "vitamina", "colageno", "hipercalorico")),
    "tech": ("Tecnologia", "📱", (
        "fone", "bluetooth", "carregador", "cabo", "usb", "celular", "smartphone", "smartwatch", "notebook", "teclado",
        "mouse", "monitor", "gamer", "headset", "webcam", "tablet", "powerbank", "ssd", "pendrive", "roteador",
        "tv", "televisao", "console", "controle", "caixa de som", "relogio inteligente", "camera", "drone",
        "projetor", "lampada inteligente", "alexa", "cartao de memoria", "adaptador", "hub", "mousepad", "impressora")),
    "casa": ("Casa e cozinha", "🏠", (
        "air fryer", "fritadeira", "panela", "frigideira", "cozinha", "organizador", "decoracao", "luminaria",
        "lencol", "toalha", "aspirador", "ventilador", "cortina", "tapete", "faca", "garrafa", "termica", "pote",
        "liquidificador", "cafeteira", "mop", "vassoura", "limpeza", "utensilio", "edredom", "travesseiro", "colcha",
        "jogo de cama", "sanduicheira", "batedeira", "microondas", "geladeira", "fogao", "cesto", "prateleira", "varal",
        "espelho", "quadro", "vaso", "tabua", "marmita", "copo", "xicara", "talher", "purificador", "umidificador")),
    "auto": ("Automóveis e motos", "🚗", (
        "carro", "automotivo", "veicular", "moto", "motocicleta", "capacete", "pneu", "calibrador", "cera automotiva",
        "polidor", "oleo", "parabrisa", "palheta", "multimidia", "dashcam", "retrovisor", "capa de banco", "volante",
        "farol", "tapete automotivo", "camera de re", "suporte veicular", "carregador veicular", "aspirador automotivo",
        "som automotivo", "alarme", "macaco", "step", "cobertura de carro", "lavadora de alta pressao", "pastilha de freio",
        "bateria automotiva", "limpa vidro", "desengraxante", "tapete de carro")),
    "beleza": ("Beleza e cuidados", "💄", (
        "perfume", "maquiagem", "shampoo", "condicionador", "creme", "hidratante", "batom", "base", "secador",
        "chapinha", "prancha", "barbeador", "barbear", "skincare", "protetor solar", "esmalte", "unha", "depilador",
        "escova secadora", "sabonete", "serum", "mascara capilar", "desodorante", "pincel", "paleta", "aparador")),
    "moda": ("Moda e acessórios", "👟", (
        "tenis", "camiseta", "camisa", "calca", "bermuda", "vestido", "bolsa", "mochila", "relogio", "oculos",
        "sandalia", "chinelo", "jaqueta", "moletom", "meia", "cueca", "biquini", "short", "blusa", "saia", "bone",
        "cinto", "carteira", "pochete", "brinco", "colar", "pulseira", "sapato", "bota", "regata", "legging")),
    "pet": ("Pets", "🐶", (
        "pet", "cachorro", "gato", "racao", "coleira", "petisco", "areia higienica", "arranhador", "comedouro",
        "bebedouro pet", "caminha pet", "cao", "filhote", "aquario", "antipulga", "peitoral")),
    "bebe": ("Bebê e infantil", "🧸", (
        "bebe", "fralda", "mamadeira", "chupeta", "infantil", "crianca", "brinquedo", "lego", "boneca", "carrinho de bebe",
        "bebe conforto", "berco", "babador", "pelucia", "quebra cabeca", "kids", "escolar", "mochila infantil")),
    "ferramentas": ("Ferramentas e jardim", "🔧", (
        "furadeira", "parafusadeira", "ferramenta", "chave de fenda", "alicate", "serra", "trena", "martelo",
        "lixadeira", "mangueira", "jardim", "cortador de grama", "esmerilhadeira", "maleta de ferramentas", "nivel",
        "solda", "multimetro", "escada", "cadeado", "fechadura", "tinta", "pincel de parede")),
}


def valido(slug: str) -> bool:
    return slug in NICHOS


def nome(slug: str) -> str:
    return NICHOS.get(slug, NICHOS[GERAL])[0]


def emoji(slug: str) -> str:
    return NICHOS.get(slug, NICHOS[GERAL])[1]


def catalogo() -> list[dict]:
    return [{"slug": s, "nome": n, "emoji": e} for s, (n, e, _) in NICHOS.items()]


def _raiz(t: str) -> str:
    """Tira o plural simples (fones→fone, capacetes→capacete) para comparar singular com plural."""
    return t[:-1] if len(t) > 3 and t.endswith("s") else t


def _toks(texto: str) -> list[str]:
    return [_raiz(t) for t in re.findall(r"[a-z0-9]+", cerebro.normalizar(texto))]


def _pontos(toks: list[str], chaves: tuple[str, ...]) -> int:
    conj, frase = set(toks), " " + " ".join(toks) + " "
    n = 0
    for k in chaves:
        kt = _toks(k)
        if len(kt) == 1:
            n += kt[0] in conj
        elif " " + " ".join(kt) + " " in frase:
            n += 2  # frase pronta ("air fryer", "carregador veicular") é pista mais forte
    return n


def pontuar(busca: str, titulo: str = "") -> dict[str, int]:
    """Pontos de cada nicho para esta oferta (só os que pontuaram)."""
    tb, tt = _toks(busca), _toks(titulo)
    saida = {}
    for slug, (_, _, chaves) in NICHOS.items():
        if not chaves:
            continue
        p = 3 * _pontos(tb, chaves) + _pontos(tt, chaves)
        if p:
            saida[slug] = p
    return saida


def classificar(busca: str, titulo: str = "") -> str | None:
    """Nicho mais provável da oferta, ou None se não houver nenhuma pista. Empate: vale a ordem do catálogo."""
    p = pontuar(busca, titulo)
    if not p:
        return None
    ordem = list(NICHOS)
    return max(p, key=lambda s: (p[s], -ordem.index(s)))
