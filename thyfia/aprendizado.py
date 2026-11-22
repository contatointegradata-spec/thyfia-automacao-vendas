"""
Aprendizado do Cérebro — sem LLM, só estatística simples, explicável e que roda no próprio servidor.

O que um assistente com memória faz (lembrar, relacionar, aprender com o retorno, entregar de forma personalizada),
aqui feito com recursos próprios:

  1. MEMÓRIA DE PREÇOS   cada produto tem histórico diário de preço; queda e "menor preço" são medidos, nunca inventados.
  2. CLASSIFICADOR       Naive Bayes que aprende o nicho de palavras novas (auto-treino só com pistas confiantes e
                         correções do cliente). Palavra-chave manda; o modelo cobre o que as palavras não cobrem.
  3. FEEDBACK            cliques (link rastreado, opt-in), aprovações e "pular" viram sinal. Um bandit (média encolhida +
                         bônus de exploração que decai) aprende por cliente o que o público dele quer: nicho, busca,
                         faixa de preço e layout da mensagem. Sem sinal, não muda nada (partida a frio segura).
  4. ENTREGA             a curadoria soma os bônus aprendidos (com o motivo em português) e o compositor usa o layout
                         que mais rende, sem perder a variedade que protege contra spam.

Tudo grava o PORQUÊ, para o painel mostrar o que o Cérebro aprendeu.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import statistics
from collections import Counter
from datetime import datetime, timedelta

from . import cerebro, db, nichos

# --------------------------------------------------------------------------- 1. memória de preços
JANELA_PRECO_DIAS = 90
QUEDA_MIN_PCT = 5.0            # abaixo disso é oscilação, não promoção
OBS_MINIMAS = 2                # dias de histórico anteriores exigidos para afirmar queda


def parse_preco(v) -> float | None:
    """'R$ 1.299,90' | '99,90' | '99.90' | 99.9 → float (ou None)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    t = re.sub(r"[^\d.,]", "", str(v))
    if not t:
        return None
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        n = float(t)
    except ValueError:
        return None
    return n if n > 0 else None


def registrar_precos(resultados: list[dict], agora: datetime | None = None) -> int:
    """Guarda 1 observação por produto por dia (dados públicos). Devolve quantas gravou."""
    dia = (agora or datetime.now()).strftime("%Y-%m-%d")
    c, n = db.conn(), 0
    for r in resultados:
        p, url = parse_preco(r.get("preco")), (r.get("url") or "").strip()
        if p and url:
            c.execute("INSERT INTO preco_historico (url, dia, preco) VALUES (?,?,?) "
                      "ON CONFLICT(url, dia) DO UPDATE SET preco=excluded.preco", (url, dia, p))
            n += 1
    c.commit()
    return n


def reconstruir_precos() -> int:
    """Primeira execução: aproveita os preços que já estão nos itens gerados (histórico que já existia)."""
    c = db.conn()
    if c.execute("SELECT 1 FROM preco_historico LIMIT 1").fetchone():
        return 0
    n = c.execute(
        "INSERT OR IGNORE INTO preco_historico (url, dia, preco) "
        "SELECT url_produto, substr(criado_em,1,10), MIN(preco) FROM job_itens "
        "WHERE preco > 0 AND url_produto <> '' GROUP BY url_produto, substr(criado_em,1,10)").rowcount
    c.commit()
    return n


def precos_info(ofertas: list[dict]) -> dict[str, dict]:
    """Para cada oferta {url_produto, preco, criado_em}: o que o histórico diz do preço dela.
    {obs, ref, queda_pct, menor}. `ref` = mediana dos dias ANTERIORES ao dia em que a oferta foi vista."""
    c = db.conn()
    urls = list({o["url_produto"] for o in ofertas if o.get("url_produto")})
    hist: dict[str, list[tuple[str, float]]] = {}
    corte = (datetime.now() - timedelta(days=JANELA_PRECO_DIAS)).strftime("%Y-%m-%d")
    for i in range(0, len(urls), 400):
        lote = urls[i:i + 400]
        for r in c.execute(f"SELECT url, dia, preco FROM preco_historico WHERE dia >= ? "
                           f"AND url IN ({','.join('?' * len(lote))})", (corte, *lote)):
            hist.setdefault(r["url"], []).append((r["dia"], r["preco"]))
    saida = {}
    for o in ofertas:
        url = o.get("url_produto")
        if not url or url not in hist or not o.get("preco"):
            continue
        dia = str(o.get("criado_em") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
        ant = [p for d, p in hist[url] if d < dia]
        if len(ant) < OBS_MINIMAS:
            continue
        atual, ref = float(o["preco"]), statistics.median(ant)
        queda = round((1 - atual / ref) * 100, 1) if ref and atual <= ref * (1 - QUEDA_MIN_PCT / 100) else 0.0
        saida[url] = {"obs": len(ant), "ref": round(ref, 2), "queda_pct": queda,
                      "menor": bool(len(ant) >= 3 and atual < min(ant))}
    return saida


# --------------------------------------------------------------------------- 2. classificador de nicho (Naive Bayes)
PESO_BUSCA = 3                 # o termo que o cliente buscou vale mais que o título
SEMENTE = 2.0                  # cada palavra-chave do catálogo entra no modelo como "visto 2 vezes"
ALFA = 0.5                     # suavização de Laplace
CONFIANCA_MIN = 0.75           # só afirma o nicho se a probabilidade for maior que isso
_CLASSES = [n for n in nichos.NICHOS if n != nichos.GERAL]


def _feats(busca: str, titulo: str = "") -> Counter:
    f: Counter = Counter()
    for t in nichos._toks(busca):
        if not any(ch.isdigit() for ch in t):  # "900g", "65w", "4l": medida não diz o nicho
            f[t] += PESO_BUSCA
    for t in nichos._toks(titulo):
        if not any(ch.isdigit() for ch in t):
            f[t] += 1
    return f


def _sementes() -> dict[str, Counter]:
    saida = {}
    for slug in _CLASSES:
        cnt: Counter = Counter()
        for k in nichos.NICHOS[slug][2]:
            for t in nichos._toks(k):
                cnt[t] += SEMENTE
        saida[slug] = cnt
    return saida


_SEM = _sementes()


def treinar(busca: str, titulo: str, nicho: str, peso: float = 1.0) -> None:
    if nicho not in _CLASSES:
        return
    c = db.conn()
    for t, n in _feats(busca, titulo).items():
        c.execute("INSERT INTO nb_tokens (nicho, token, n) VALUES (?,?,?) "
                  "ON CONFLICT(nicho, token) DO UPDATE SET n=n+excluded.n", (nicho, t, n * peso))
    c.execute("INSERT INTO nb_docs (nicho, n) VALUES (?,?) ON CONFLICT(nicho) DO UPDATE SET n=n+excluded.n",
              (nicho, peso))
    c.commit()


def auto_treinar(termo: str, resultados: list[dict]) -> int:
    """Depois de cada busca: se uma palavra-chave identifica o nicho COM FOLGA, o modelo aprende as outras palavras
    do título e do termo nesse nicho (ex.: 'creatina monohidratada' → 'monohidratada' passa a indicar fit)."""
    n = 0
    for r in resultados:
        titulo = r.get("titulo", "")
        p = sorted(nichos.pontuar(termo, titulo).items(), key=lambda x: -x[1])
        if p and p[0][1] >= 3 and (len(p) == 1 or p[0][1] - p[1][1] >= 3):
            treinar(termo, titulo, p[0][0])
            n += 1
    return n


class Modelo:
    """Contagens do Naive Bayes (sementes + o que foi aprendido), carregadas uma vez por uso."""

    def __init__(self):
        self.tok = {s: Counter(self_) for s, self_ in _SEM.items()}
        self.docs = {s: 1.0 for s in _CLASSES}
        c = db.conn()
        for r in c.execute("SELECT nicho, token, n FROM nb_tokens"):
            if r["nicho"] in self.tok:
                self.tok[r["nicho"]][r["token"]] += r["n"]
        for r in c.execute("SELECT nicho, n FROM nb_docs"):
            if r["nicho"] in self.docs:
                self.docs[r["nicho"]] += r["n"]
        self.vocab = set().union(*[set(v) for v in self.tok.values()])
        self.total = {s: sum(v.values()) for s, v in self.tok.items()}

    def prever(self, busca: str, titulo: str = "") -> tuple[str | None, float]:
        f = {t: n for t, n in _feats(busca, titulo).items() if t in self.vocab}
        if not f:
            return None, 0.0
        V, soma_docs = len(self.vocab), sum(self.docs.values())
        logs = {}
        for s in _CLASSES:
            lp = math.log(self.docs[s] / soma_docs)
            den = self.total[s] + ALFA * V
            for t, n in f.items():
                lp += n * math.log((self.tok[s].get(t, 0.0) + ALFA) / den)
            logs[s] = lp
        m = max(logs.values())
        exps = {s: math.exp(v - m) for s, v in logs.items()}
        z = sum(exps.values())
        melhor = max(exps, key=exps.get)
        conf = exps[melhor] / z
        return (melhor, round(conf, 3)) if conf >= CONFIANCA_MIN else (None, round(conf, 3))


class Resolvedor:
    """Nicho de cada oferta: 1) correção do cliente → 2) palavra-chave → 3) o que o Cérebro aprendeu."""

    def __init__(self, uid: int | None):
        self.uid = uid
        self.correcoes = {}
        if uid is not None:
            self.correcoes = {r["busca"]: r["nicho"] for r in
                              db.conn().execute("SELECT busca, nicho FROM nicho_busca WHERE usuario_id=?", (uid,))}
        self._modelo: Modelo | None = None

    def de(self, busca: str, titulo: str = "") -> tuple[str | None, str]:
        n = self.correcoes.get(cerebro.normalizar(busca))
        if n:
            return n, "correção sua"
        n = nichos.classificar(busca, titulo)
        if n:
            return n, "palavra-chave"
        if self._modelo is None:
            self._modelo = Modelo()
        n, _ = self._modelo.prever(busca, titulo)
        return (n, "aprendido") if n else (None, "")


def nicho_de(uid: int | None, busca: str, titulo: str = "") -> tuple[str | None, str]:
    return Resolvedor(uid).de(busca, titulo)


def corrigir_nicho(uid: int, busca: str, nicho: str | None) -> None:
    """O cliente diz de que nicho é uma busca (ou '' para voltar ao automático). O Cérebro aprende com isso."""
    b = cerebro.normalizar(busca)
    if not b:
        return
    c = db.conn()
    if not nicho or nicho == nichos.GERAL:
        c.execute("DELETE FROM nicho_busca WHERE usuario_id=? AND busca=?", (uid, b))
        c.commit()
        return
    if not nichos.valido(nicho):
        raise ValueError("Nicho inválido.")
    c.execute("INSERT INTO nicho_busca (usuario_id, busca, nicho) VALUES (?,?,?) "
              "ON CONFLICT(usuario_id, busca) DO UPDATE SET nicho=excluded.nicho", (uid, b, nicho))
    c.commit()
    treinar(b, "", nicho, peso=2.0)  # correção humana pesa mais que o auto-treino


# --------------------------------------------------------------------------- 3. feedback → bandit
MATURACAO_H = 24               # um envio só é avaliado depois disso (dá tempo de o público clicar)
PESO_CLIQUE = 1.0              # clique medido: evidência cheia
PESO_APROVACAO = 0.5           # aprovar/pular: gosto do dono, evidência menor
PESOS_DIM = {"nicho": 8.0, "busca": 12.0, "faixa": 6.0}   # pontos máximos que cada dimensão move na curadoria
ENCOLHE = 4.0                  # quantos "envios fictícios na média" puxam estimativas de pouca amostra
MIN_TOTAL = 3.0                # sem pelo menos isso de evidência na dimensão, o bandit não mexe em nada
EXPLORA_MAX = 4.0              # pontos de bônus para busca ainda sem dados (decai com a amostra)
LAYOUTS = ("A", "B", "C")
MIN_VARIANTE = 9.0             # amostra total antes de preferir um layout
FRACAO_LIVRE = 0.35            # parte das mensagens que continua com layout aleatório (variedade)


def faixa_de(preco) -> str:
    p = float(preco or 0)
    for lim, nome in ((30, "ate30"), (80, "30a80"), (200, "80a200"), (500, "200a500")):
        if p < lim:
            return nome
    return "500mais"


NOME_FAIXA = {"ate30": "até R$ 30", "30a80": "R$ 30–80", "80a200": "R$ 80–200", "200a500": "R$ 200–500",
              "500mais": "acima de R$ 500"}


class Bandit:
    def __init__(self, uid: int):
        self.d: dict[str, dict[str, tuple[float, float]]] = {}
        for r in db.conn().execute("SELECT dimensao, chave, tentativas, sucessos FROM cerebro_bandit "
                                   "WHERE usuario_id=?", (uid,)):
            self.d.setdefault(r["dimensao"], {})[r["chave"]] = (r["tentativas"], r["sucessos"])

    def totais(self, dim: str) -> tuple[float, float]:
        v = self.d.get(dim, {}).values()
        return sum(t for t, _ in v), sum(s for _, s in v)

    def get(self, dim: str, chave: str) -> tuple[float, float]:
        return self.d.get(dim, {}).get(chave, (0.0, 0.0))

    def estimativa(self, dim: str, chave: str) -> tuple[float, float, float, float] | None:
        """(média encolhida, média geral, tentativas, bônus) ou None se ainda não há evidência na dimensão."""
        tot, suc = self.totais(dim)
        if tot < MIN_TOTAL:
            return None
        base = suc / tot
        t, s = self.get(dim, chave)
        return (s + base * ENCOLHE) / (t + ENCOLHE), base, t, tot

    def lift(self, dim: str, chave: str) -> tuple[float, float, float, float] | None:
        e = self.estimativa(dim, chave)
        if e is None:
            return None
        m, base, t, _ = e
        return max(-1.0, min(1.0, (m - base) / (base + 0.05))), m, base, t


def registrar_resultado(uid: int, nicho: str | None, busca: str, preco, variante: str | None,
                        sucesso: bool, peso: float = PESO_CLIQUE) -> None:
    chaves = [("busca", cerebro.normalizar(busca)), ("faixa", faixa_de(preco))]
    if nicho:
        chaves.append(("nicho", nicho))
    if variante:
        chaves.append(("variante", variante))
    c, agora = db.conn(), datetime.now().isoformat(timespec="seconds")
    for dim, chave in chaves:
        if chave:
            c.execute("INSERT INTO cerebro_bandit (usuario_id, dimensao, chave, tentativas, sucessos, atualizado) "
                      "VALUES (?,?,?,?,?,?) ON CONFLICT(usuario_id, dimensao, chave) DO UPDATE SET "
                      "tentativas=tentativas+excluded.tentativas, sucessos=sucessos+excluded.sucessos, "
                      "atualizado=excluded.atualizado", (uid, dim, chave, peso, peso if sucesso else 0.0, agora))
    c.commit()


def maturar(uid: int, agora: datetime | None = None) -> int:
    """Avalia os envios com link rastreado que já passaram da maturação: teve clique (sucesso) ou não (falha)?"""
    agora = agora or datetime.now().astimezone()
    corte = (agora - timedelta(hours=MATURACAO_H)).isoformat(timespec="seconds")
    c, n = db.conn(), 0
    for e in c.execute("SELECT e.id, e.nicho, e.busca, e.preco, e.variante, "
                       "(SELECT COUNT(*) FROM piloto_cliques k WHERE k.envio_id=e.id) cliques "
                       "FROM piloto_envios e WHERE e.usuario_id=? AND e.status='enviado' AND e.avaliado=0 "
                       "AND e.token IS NOT NULL AND e.enviado_em <= ?", (uid, corte)).fetchall():
        registrar_resultado(uid, e["nicho"], e["busca"], e["preco"], e["variante"], e["cliques"] > 0)
        c.execute("UPDATE piloto_envios SET avaliado=1 WHERE id=?", (e["id"],))
        n += 1
    c.commit()
    return n


def bonus(b: Bandit, oferta: dict) -> tuple[float, list[str]]:
    """Pontos extras (ou descontos) que o histórico DESTE público dá a uma oferta, com os motivos."""
    pts, motivos = 0.0, []
    busca = cerebro.normalizar(oferta.get("busca", ""))
    for dim, chave, rotulo in (("nicho", oferta.get("nicho"), nichos.nome(oferta.get("nicho") or "")),
                               ("busca", busca, f"“{oferta.get('busca', '')}”"),
                               ("faixa", faixa_de(oferta.get("preco")), NOME_FAIXA[faixa_de(oferta.get("preco"))])):
        if not chave:
            continue
        r = b.lift(dim, chave)
        if r is None:
            continue
        lift, m, base, t = r
        p = lift * PESOS_DIM[dim]
        pts += p
        if abs(p) >= 3 and t >= 2:
            motivos.append(f"seu público responde {'melhor' if p > 0 else 'pior'} a {rotulo} "
                           f"({round(100 * m)}% contra {round(100 * base)}% da média)")
    if b.estimativa("busca", busca) is not None:
        t = b.get("busca", busca)[0]
        ex = EXPLORA_MAX * math.sqrt(1.0 / (t + 1))
        if t < 5 and ex >= 1.5:
            pts += ex
            motivos.append(f"explorando “{oferta.get('busca', '')}”: ainda há pouco retorno do seu público")
    return round(pts, 2), motivos


def escolher_variante(uid: int, rnd) -> str | None:
    """Layout que mais rende para este público; None = deixa o compositor variar sozinho (partida a frio e variedade)."""
    b = Bandit(uid)
    tot, _ = b.totais("variante")
    if tot < MIN_VARIANTE or rnd.random() < FRACAO_LIVRE:
        return None
    melhor, nota = None, -1.0
    for v in LAYOUTS:
        e = b.estimativa("variante", v)
        if e is None:
            return None
        m, _, t, total = e
        s = m + 0.3 * math.sqrt(math.log(total + 1) / (t + 1))
        if s > nota:
            melhor, nota = v, s
    return melhor


# --------------------------------------------------------------------------- 4. cliques rastreados
_ROBOS = re.compile(r"bot|crawler|spider|preview|whatsapp|facebookexternalhit|telegram|slurp|curl|wget|python-requests|"
                    r"headless|http-client|okhttp|go-http|vkshare|embedly|skypeuri", re.I)


def novo_token() -> str:
    return secrets.token_urlsafe(6)


def eh_robo(user_agent: str) -> bool:
    return not user_agent or bool(_ROBOS.search(user_agent))


def registrar_clique(token: str, ip: str, user_agent: str) -> str | None:
    """Devolve o link de afiliado para redirecionar (ou None se o token não existe). Só conta gente de verdade:
    robôs de pré-visualização (WhatsApp, Telegram…) ficam de fora e o mesmo visitante conta uma vez por envio."""
    c = db.conn()
    e = c.execute("SELECT id, usuario_id, link_real, link FROM piloto_envios WHERE token=?", (token,)).fetchone()
    if not e:
        return None
    destino = e["link_real"] or e["link"]
    if not destino.startswith(("http://", "https://")):
        return None
    if not eh_robo(user_agent):
        h = hashlib.sha256(f"{ip}|{user_agent}|thyfia".encode()).hexdigest()[:16]
        c.execute("INSERT OR IGNORE INTO piloto_cliques (envio_id, usuario_id, visitante, criado_em) VALUES (?,?,?,?)",
                  (e["id"], e["usuario_id"], h, datetime.now().isoformat(timespec="seconds")))
        c.commit()
    return destino


# --------------------------------------------------------------------------- painel: o que o Cérebro aprendeu
def _rotulo(dim: str, chave: str) -> str:
    if dim == "nicho":
        return f"{nichos.emoji(chave)} {nichos.nome(chave)}"
    if dim == "faixa":
        return NOME_FAIXA.get(chave, chave)
    if dim == "variante":
        return f"Layout {chave}"
    return chave


def resumo(uid: int) -> dict:
    c = db.conn()
    b = Bandit(uid)
    arms = {}
    for dim in ("nicho", "busca", "faixa", "variante"):
        linhas = [{"chave": k, "rotulo": _rotulo(dim, k), "tentativas": round(t, 1), "taxa": round(100 * s / t) if t else 0}
                  for k, (t, s) in b.d.get(dim, {}).items() if t >= 1]
        arms[dim] = sorted(linhas, key=lambda x: (-x["taxa"], -x["tentativas"]))[:6]
    tot, suc = b.totais("nicho")
    tot_b, _ = b.totais("busca")
    cfg = c.execute("SELECT rastrear FROM piloto_config WHERE usuario_id=?", (uid,)).fetchone()
    cliques = c.execute("SELECT COUNT(*) FROM piloto_cliques WHERE usuario_id=?", (uid,)).fetchone()[0]
    amostra = max(tot, tot_b)
    fase = "sem_dados" if amostra < MIN_TOTAL else "aprendendo" if amostra < 30 else "calibrado"

    m = Modelo()
    aprendido = {}
    for s in _CLASSES:
        extra = [(t, n) for t, n in m.tok[s].items() if t not in _SEM[s] and n >= 3]
        extra.sort(key=lambda x: -x[1])
        if extra:
            aprendido[s] = {"nome": nichos.nome(s), "emoji": nichos.emoji(s), "total": len(extra),
                            "palavras": [t for t, _ in extra[:6]]}

    quedas = []
    hoje = datetime.now().strftime("%Y-%m-%d")
    recentes = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for r in c.execute("SELECT h.url, COALESCE(NULLIF(p.titulo,''), (SELECT titulo FROM job_itens WHERE url_produto=h.url "
                       "LIMIT 1), h.url) titulo FROM (SELECT DISTINCT url FROM preco_historico WHERE dia >= ?) h "
                       "LEFT JOIN produtos p ON p.url = h.url LIMIT 400", (recentes,)):
        pts = c.execute("SELECT dia, preco FROM preco_historico WHERE url=? ORDER BY dia", (r["url"],)).fetchall()
        if len(pts) < OBS_MINIMAS + 1:
            continue
        atual, ant = pts[-1]["preco"], [x["preco"] for x in pts[:-1]]
        ref = statistics.median(ant)
        if ref and atual <= ref * (1 - QUEDA_MIN_PCT / 100):
            quedas.append({"titulo": (r["titulo"] or r["url"])[:90], "de": round(ref, 2), "para": round(atual, 2),
                           "pct": round((1 - atual / ref) * 100)})
    quedas.sort(key=lambda x: -x["pct"])
    return {
        "fase": fase,
        "rastrear": bool(cfg and cfg["rastrear"]),
        "cliques": {"total": cliques, "avaliados": round(tot), "acertos": round(suc), "taxa": round(100 * suc / tot) if tot else 0},
        "preferencias": arms,
        "palavras_aprendidas": aprendido,
        "precos": {"produtos_com_historico": c.execute(
            "SELECT COUNT(*) FROM (SELECT url FROM preco_historico GROUP BY url HAVING COUNT(*) >= 2)").fetchone()[0],
            "quedas": quedas[:5]},
        "hoje": hoje,
    }
