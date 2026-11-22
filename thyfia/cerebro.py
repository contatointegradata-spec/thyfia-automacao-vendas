"""
Cérebro da ThyfIA — motor de recuperação + aprendizado coletivo (sem LLM).

Cada busca de qualquer usuário alimenta uma base de conhecimento compartilhada:
  • produtos vistos (título, preço) e se o link de afiliado foi gerado ou falhou
  • termos buscados, quantas vezes e com que taxa de sucesso
  • associação termo → produto

Com isso a ThyfIA:
  1. RECUPERA produtos relevantes da base (ranking BM25 nos títulos) para completar
     buscas e responder mais rápido;
  2. FILTRA produtos que já se mostraram inelegíveis ao programa de afiliados;
  3. RANQUEIA primeiro os produtos que já geraram link com sucesso;
  4. SUGERE termos (autocomplete) e buscas relacionadas aprendidas com todos.
Quanto mais gente usa, maior a base e melhores ficam as respostas.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from datetime import datetime

from . import db

STOP = set(
    "de da do das dos para pra com sem e o a os as um uma em no na nos nas por p/ c/ "
    "kit cor cores novo nova original promocao oferta frete gratis unidade unidades".split()
)


# --------------------------------------------------------------------------- texto
def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return re.sub(r"\s+", " ", t).strip()


def tokens(texto: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", normalizar(texto)) if len(w) > 1 and w not in STOP]


def _inelegivel(row) -> bool:
    return row["falhas"] >= 2 and row["gerados"] == 0


# --------------------------------------------------------------------------- aprendizado
def aprender_busca(termo: str, resultados: list[dict]) -> None:
    """resultados: [{url, titulo, preco, ok: bool}]"""
    t = normalizar(termo)
    agora = datetime.now().isoformat(timespec="seconds")
    ok = sum(1 for r in resultados if r.get("ok"))
    c = db.conn()
    c.execute(
        "INSERT INTO termos (termo, buscas, links_ok, links_total, ultima) VALUES (?,1,?,?,?) "
        "ON CONFLICT(termo) DO UPDATE SET buscas=buscas+1, links_ok=links_ok+excluded.links_ok, "
        "links_total=links_total+excluded.links_total, ultima=excluded.ultima",
        (t, ok, len(resultados), agora),
    )
    for r in resultados:
        gerou = 1 if r.get("ok") else 0
        c.execute(
            "INSERT INTO produtos (url, titulo, titulo_norm, preco, vistos, gerados, falhas, atualizado) "
            "VALUES (?,?,?,?,1,?,?,?) ON CONFLICT(url) DO UPDATE SET "
            "titulo=COALESCE(NULLIF(excluded.titulo,''), titulo), "
            "titulo_norm=COALESCE(NULLIF(excluded.titulo_norm,''), titulo_norm), "
            "preco=COALESCE(NULLIF(excluded.preco,''), preco), vistos=vistos+1, "
            "gerados=gerados+excluded.gerados, falhas=falhas+excluded.falhas, atualizado=excluded.atualizado",
            (r["url"], r.get("titulo", ""), " ".join(tokens(r.get("titulo", ""))),
             r.get("preco", ""), gerou, 1 - gerou, agora),
        )
        c.execute(
            "INSERT INTO produto_termos (url, termo) VALUES (?,?) "
            "ON CONFLICT(url, termo) DO UPDATE SET vezes=vezes+1",
            (r["url"], t),
        )
    c.commit()
    try:  # memória de preços e vocabulário de nichos: aprendem junto, sem nunca atrapalhar a busca
        from . import aprendizado
        aprendizado.registrar_precos(resultados)
        aprendizado.auto_treinar(t, resultados)
    except Exception:
        pass


# --------------------------------------------------------------------------- recuperação
def ranquear(produtos: list[dict]) -> list[dict]:
    """Remove inelegíveis conhecidos e coloca na frente os que já geraram link."""
    if not produtos:
        return produtos
    c = db.conn()
    marcas = "?" * len(produtos)
    rows = c.execute(
        f"SELECT url, gerados, falhas FROM produtos WHERE url IN ({','.join(marcas)})",
        [p["url"] for p in produtos],
    ).fetchall()
    info = {r["url"]: r for r in rows}
    filtrados = [p for p in produtos if not (p["url"] in info and _inelegivel(info[p["url"]]))]
    return sorted(filtrados, key=lambda p: -(info[p["url"]]["gerados"] if p["url"] in info else 0))


def recuperar(termo: str, k: int, excluir: set[str] | None = None) -> list[dict]:
    """BM25 sobre os títulos da base de conhecimento."""
    toks = tokens(termo)
    if not toks or k <= 0:
        return []
    excluir = excluir or set()
    c = db.conn()
    where = " OR ".join("titulo_norm LIKE ?" for _ in toks)
    rows = c.execute(
        f"SELECT url, titulo, preco, titulo_norm, gerados, falhas FROM produtos WHERE {where}",
        [f"%{t}%" for t in toks],
    ).fetchall()
    if not rows:
        return []
    termo_n = normalizar(termo)
    ligados = {
        r["url"] for r in c.execute("SELECT url FROM produto_termos WHERE termo=?", (termo_n,))
    }
    n_total = max(c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0], 1)
    docs = [(r, (r["titulo_norm"] or "").split()) for r in rows]
    media = sum(len(p) for _, p in docs) / len(docs) or 1
    df = {t: sum(1 for _, p in docs if t in p) for t in toks}

    pontuados = []
    for r, palavras in docs:
        if r["url"] in excluir or _inelegivel(r):
            continue
        s = 0.0
        for t in toks:
            f = palavras.count(t)
            if f:
                idf = math.log(1 + (n_total - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * len(palavras) / media))
        if s <= 0:
            continue
        s *= 1 + math.log1p(r["gerados"])
        if r["url"] in ligados:
            s *= 1.5
        pontuados.append((s, r))
    pontuados.sort(key=lambda x: -x[0])
    return [
        {"url": r["url"], "titulo": r["titulo"] or "", "preco": r["preco"] or "", "origem": "cerebro"}
        for _, r in pontuados[:k]
    ]


# --------------------------------------------------------------------------- sugestões
def sugerir(q: str, n: int = 8) -> list[dict]:
    qn = normalizar(q)
    c = db.conn()
    if qn:
        rows = c.execute(
            "SELECT * FROM termos WHERE termo LIKE ? LIMIT 200", (f"%{qn}%",)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM termos ORDER BY buscas DESC LIMIT 50").fetchall()

    def score(r):
        taxa = r["links_ok"] / r["links_total"] if r["links_total"] else 0.5
        return r["buscas"] * (0.5 + taxa) + (5 if qn and r["termo"].startswith(qn) else 0)

    rows = sorted(rows, key=score, reverse=True)[:n]
    return [
        {
            "termo": r["termo"],
            "buscas": r["buscas"],
            "taxa": round(100 * r["links_ok"] / r["links_total"]) if r["links_total"] else None,
        }
        for r in rows
    ]


def relacionados(termo: str, n: int = 6) -> list[str]:
    """Combina o termo com as palavras que mais aparecem nos produtos ligados a ele."""
    toks = set(tokens(termo))
    if not toks:
        return []
    base = recuperar(termo, 60)
    contagem: Counter[str] = Counter()
    for p in base:
        for w in set(tokens(p["titulo"])):
            if w not in toks and not w.isdigit() and len(w) > 2:
                contagem[w] += 1
    termo_n = normalizar(termo)
    return [f"{termo_n} {w}" for w, qtd in contagem.most_common(n) if qtd >= 2]


def estatisticas() -> dict:
    c = db.conn()
    um = lambda sql: c.execute(sql).fetchone()[0] or 0  # noqa: E731
    top = c.execute(
        "SELECT termo, buscas, links_ok, links_total FROM termos ORDER BY buscas DESC, links_ok DESC LIMIT 8"
    ).fetchall()
    total_links = um("SELECT SUM(links_total) FROM termos")
    ok_links = um("SELECT SUM(links_ok) FROM termos")
    return {
        "produtos": um("SELECT COUNT(*) FROM produtos"),
        "elegiveis": um("SELECT COUNT(*) FROM produtos WHERE gerados > 0"),
        "termos": um("SELECT COUNT(*) FROM termos"),
        "buscas": um("SELECT SUM(buscas) FROM termos"),
        "links": ok_links,
        "taxa": round(100 * ok_links / total_links) if total_links else 0,
        "top": [
            {
                "termo": r["termo"],
                "buscas": r["buscas"],
                "taxa": round(100 * r["links_ok"] / r["links_total"]) if r["links_total"] else 0,
            }
            for r in top
        ],
    }


# --------------------------------------------------------------------------- curadoria de ofertas (Piloto Automático)
def _idade_dias(criado_em: str | None, agora: datetime) -> float:
    try:
        d = datetime.fromisoformat(str(criado_em or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=agora.tzinfo)
    return max((agora - d).total_seconds() / 86400, 0.0)


def curar_ofertas(ofertas: list[dict], *, ultima_busca: str = "", envios_por_busca: dict[str, int] | None = None,
                  validade_dias: float = 7, agora: datetime | None = None, uid: int | None = None) -> list[dict]:
    """Ranqueia as ofertas do cliente para o Piloto Automático. Cada oferta:
    {id, busca, titulo, preco, url_produto, link_afiliado, imagem, criado_em}.

    O Cérebro decide com o que aprendeu de TODA a comunidade (sem LLM, 100% explicável):
      • descarta o que a comunidade já viu falhar (produto inelegível ao programa de afiliados);
      • premia produto com link já validado (`gerados`) e busca com boa taxa de sucesso;
      • premia o que está fresco (preço envelhece) e o preço abaixo da mediana da própria busca;
      • diversifica: evita repetir a categoria do último envio e dá vez à categoria menos usada;
      • confere o histórico de preços: queda real e "menor preço já visto" ganham pontos (e viram frase na mensagem);
      • com `uid`: soma o que o público DESTE cliente já mostrou preferir (nicho, busca, faixa de preço), aprendido
        de cliques medidos e aprovações — com bônus de exploração que decai para não prender em poucos assuntos.
    Devolve cópias com `pontos` e `motivos` (lista de frases), da melhor para a pior."""
    from datetime import timezone

    if not ofertas:
        return []
    agora = agora or datetime.now(timezone.utc)
    envios_por_busca = envios_por_busca or {}
    c = db.conn()

    urls = [o["url_produto"] for o in ofertas if o.get("url_produto")]
    produtos: dict[str, object] = {}
    for i in range(0, len(urls), 400):
        lote = urls[i:i + 400]
        for r in c.execute(
            f"SELECT url, gerados, falhas FROM produtos WHERE url IN ({','.join('?' * len(lote))})", lote
        ):
            produtos[r["url"]] = r
    termos = {r["termo"]: r for r in c.execute("SELECT termo, links_ok, links_total FROM termos")}
    from . import aprendizado
    precos = aprendizado.precos_info(ofertas)
    bandit = aprendizado.Bandit(uid) if uid is not None else None

    por_busca: dict[str, list[float]] = {}
    for o in ofertas:
        por_busca.setdefault(normalizar(o.get("busca", "")), []).append(float(o["preco"]))
    medianas = {k: sorted(v)[len(v) // 2] for k, v in por_busca.items()}
    menos_usada = min((envios_por_busca.get(k, 0) for k in por_busca), default=0)
    ultima = normalizar(ultima_busca)

    saida = []
    for o in ofertas:
        p = produtos.get(o.get("url_produto"))
        if p is not None and _inelegivel(p):
            continue  # a comunidade já viu esse produto falhar: não gasta um envio com ele
        busca = normalizar(o.get("busca", ""))
        pontos, motivos = 50.0, []

        if p is not None and p["gerados"] > 0:
            pontos += min(10 * math.log1p(p["gerados"]), 15)
            motivos.append(f"link já validado {p['gerados']}x pela comunidade")
        t = termos.get(busca)
        if t is not None and t["links_total"]:
            taxa = t["links_ok"] / t["links_total"]
            pontos += 10 * taxa
            if taxa >= 0.7:
                motivos.append(f"busca “{o.get('busca', '')}” converte links em {round(100 * taxa)}% dos casos")

        idade = _idade_dias(o.get("criado_em"), agora)
        frescor = max(0.0, 1 - idade / max(validade_dias, 1))
        pontos += 20 * frescor
        if idade < 1:
            motivos.append("encontrada hoje")
        elif frescor >= 0.5:
            motivos.append(f"encontrada há {int(idade)} dia(s)")

        med = medianas.get(busca) or 0
        if med and float(o["preco"]) < med:
            pontos += min(15 * (1 - float(o["preco"]) / med) * 2, 15)
            motivos.append("preço abaixo da mediana da própria busca")

        info = precos.get(o.get("url_produto")) or {}
        if info.get("queda_pct"):
            pontos += min(info["queda_pct"] * 1.2, 20)
            motivos.append(f"preço caiu {round(info['queda_pct'])}% em relação ao histórico (era ~R$ {info['ref']:.2f})")
        if info.get("menor"):
            pontos += 6
            motivos.append("menor preço que a ThyfIA já registrou para este produto")

        if o.get("imagem"):
            pontos += 5
        if ultima and busca == ultima:
            pontos -= 25
            motivos.append("mesma categoria do último envio (rebaixada)")
        elif envios_por_busca.get(busca, 0) <= menos_usada:
            pontos += 10
            motivos.append("categoria menos divulgada recentemente")

        if bandit is not None:
            extra, mot = aprendizado.bonus(bandit, o)
            pontos += extra
            motivos += mot
        saida.append({**o, "pontos": round(pontos, 1), "motivos": motivos,
                      "queda_pct": info.get("queda_pct", 0.0), "preco_ref": info.get("ref"),
                      "menor_preco": bool(info.get("menor"))})
    saida.sort(key=lambda x: (-x["pontos"], -int(x.get("id") or 0)))
    return saida
