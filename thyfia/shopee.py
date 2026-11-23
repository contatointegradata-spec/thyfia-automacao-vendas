"""
Shopee Afiliados: busca de produtos e geração de links pela API OFICIAL (Open API, GraphQL).

Diferente do Mercado Livre (que só responde ao navegador logado da pessoa e bloqueia o servidor), a Shopee
oferece uma API própria para afiliados. Então aqui NÃO há navegador, extensão, cookie nem captcha:

  * busca:  `productOfferV2(keyword, sortType, page, limit)` devolve só produtos que estão no programa de afiliados,
            já com preço, vendas, nota, foto, comissão e o link de afiliado (`offerLink`).
  * links:  `generateShortLink(originUrl, subIds)` cria o link curto de afiliado, com até 5 sub_ids (aparecem no
            relatório de conversões da Shopee, para a pessoa saber qual busca da ThyfIA vendeu).

Cada pessoa usa as PRÓPRIAS credenciais (App ID + Secret, geradas em Portal do Afiliado > Open API): a comissão vai
para a conta dela. O Secret fica cifrado no cofre; nunca vai ao navegador nem aos logs.

Autenticação: `Authorization: SHA256 Credential={AppId}, Timestamp={ts}, Signature={sha256(AppId+ts+corpo+Secret)}`,
onde `corpo` é exatamente o JSON enviado. Códigos de erro: 10020 assinatura/credencial inválida, 10030 limite de
consultas, 10035 conta sem acesso à Open API, 10000 instabilidade, 10010/11001 consulta ou parâmetros inválidos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

import requests

from . import cerebro, cofre, db, itens

log = logging.getLogger("thyfia.shopee")

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
TIMEOUT = 20
TENTATIVAS = 3
ESPERA_BASE = 1.5  # segundos; dobra a cada nova tentativa (limite de consultas / instabilidade)
LOTE_LINKS = 10  # links curtos pedidos por chamada
POR_BUSCA_MAX = 50

ORDEM_RELEVANCIA, ORDEM_VENDAS, ORDEM_PRECO_MAIOR, ORDEM_PRECO_MENOR, ORDEM_COMISSAO = 1, 2, 3, 4, 5

CAMPOS_COMPLETOS = ("itemId productName productLink offerLink imageUrl priceMin priceMax priceDiscountRate sales "
                    "ratingStar commissionRate shopId shopName shopType")
CAMPOS_BASICOS = "itemId productName productLink offerLink imageUrl priceMin priceMax shopId shopName"

_dormir = time.sleep  # trocado nos testes
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


class ErroShopee(Exception):
    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


# --------------------------------------------------------------------------- tradução de erros
MENSAGENS = {
    10020: "A Shopee recusou as credenciais. Confira o App ID e o Secret (copie de novo, sem espaços nas pontas).",
    10030: "A Shopee limitou as consultas por um momento. Tente de novo em instantes.",
    10035: ("Sua conta de afiliado ainda não tem acesso à Open API da Shopee. No Portal do Afiliado, abra o menu "
            "Open API e solicite o acesso; depois volte aqui."),
    10000: "A Shopee está instável agora. Tente de novo em instantes.",
}
CODIGOS_CONSULTA = (10010, 11001)  # a Shopee não entendeu a consulta (campo ou parâmetro)
CODIGOS_CREDENCIAL = (10020, 10035)  # a conexão deixou de valer: a pessoa precisa agir


def _traduzir(codigo: int | None, texto: str = "") -> str:
    if codigo in MENSAGENS:
        return MENSAGENS[codigo]
    base = " ".join((texto or "").split())[:160]
    return f"A Shopee recusou o pedido ({codigo or 'sem código'}){': ' + base if base else ''}."


# --------------------------------------------------------------------------- chamada à API
def _assinar(app_id: str, secret: str, corpo: str, ts: int) -> str:
    return hashlib.sha256(f"{app_id}{ts}{corpo}{secret}".encode()).hexdigest()


def _chamar(app_id: str, secret: str, consulta: str) -> dict:
    """Uma consulta GraphQL assinada. Repete com espera quando a Shopee pede calma. Devolve o campo `data`."""
    ultimo: ErroShopee | None = None
    for tentativa in range(TENTATIVAS):
        if tentativa:
            _dormir(ESPERA_BASE * 2 ** (tentativa - 1))
        # corpo só com ASCII: o texto assinado é byte a byte o texto enviado
        corpo = json.dumps({"query": consulta}, separators=(",", ":"), ensure_ascii=True)
        ts = int(time.time())
        cab = {"Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={_assinar(app_id, secret, corpo, ts)}",
               "Content-Type": "application/json", "User-Agent": "thyfia/1.0"}
        try:
            r = requests.post(ENDPOINT, data=corpo.encode("ascii"), headers=cab, timeout=TIMEOUT)
        except requests.RequestException as e:
            ultimo = ErroShopee(f"Não consegui falar com a Shopee ({type(e).__name__}). Tente de novo em instantes.")
            continue
        try:
            resp = r.json()
        except ValueError:
            resp = {}
        erros = resp.get("errors") if isinstance(resp, dict) else None
        if erros:
            ext = (erros[0].get("extensions") or {}) if isinstance(erros[0], dict) else {}
            try:
                codigo = int(ext.get("code"))
            except (TypeError, ValueError):
                codigo = None
            texto = str(ext.get("message") or (erros[0].get("message") if isinstance(erros[0], dict) else "") or "")
            ultimo = ErroShopee(_traduzir(codigo, texto), codigo)
            if codigo in (10030, 10000):
                continue  # vale esperar e tentar de novo
            ultimo.detalhe = texto  # type: ignore[attr-defined]
            raise ultimo
        if not r.ok:
            ultimo = ErroShopee(f"A Shopee respondeu com erro HTTP {r.status_code}. Tente de novo em instantes.")
            if r.status_code in (429, 500, 502, 503, 504):
                continue
            raise ultimo
        return (resp.get("data") if isinstance(resp, dict) else None) or {}
    raise ultimo or ErroShopee("A Shopee não respondeu.")


# --------------------------------------------------------------------------- montagem da consulta
def _texto_gql(s: str) -> str:
    return json.dumps(s, ensure_ascii=True)  # literal de texto GraphQL seguro (aspas e barras escapadas)


def limpar_termo(termo: str) -> str:
    t = re.sub(r"[^\w\s.,+&/-]", " ", termo or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()[:80]


def consulta_busca(termo: str, limite: int, ordem: int, campos: str, pagina: int = 1) -> str:
    return (f"{{ productOfferV2(keyword: {_texto_gql(limpar_termo(termo))}, sortType: {int(ordem)}, "
            f"page: {int(pagina)}, limit: {int(limite)}) {{ nodes {{ {campos} }} pageInfo {{ page limit hasNextPage }} }} }}")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", cerebro.normalizar(s)).strip("-")[:24] or "busca"


def consulta_links(urls: list[str], sub_ids: list[str]) -> str:
    subs = "[" + ", ".join(_texto_gql(x) for x in sub_ids) + "]" if sub_ids else "[]"
    partes = [f"l{i}: generateShortLink(input: {{originUrl: {_texto_gql(u)}, subIds: {subs}}}) {{ shortLink }}"
              for i, u in enumerate(urls)]
    return "mutation { " + " ".join(partes) + " }"


def _sub_ids(*valores: str) -> list[str]:
    """Até 5 sub_ids só com letras, números, hífen e sublinhado (aparecem no relatório de conversões)."""
    saida = []
    for v in valores[:5]:
        v = re.sub(r"[^A-Za-z0-9_-]", "", str(v or ""))[:40]
        if v:
            saida.append(v)
    return saida


# --------------------------------------------------------------------------- leitura dos produtos
def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        n = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def _preco(v) -> float | None:
    n = _num(v)
    if n is None or n <= 0:
        return None
    return round(n / 100000, 2) if n >= 1e7 else round(n, 2)  # alguns campos da Shopee vêm em micro-unidades


def _taxa(v) -> float:
    """Comissão como fração (0,12 = 12%). A Shopee manda em decimal; se vier em percentual (12), converte."""
    n = _num(v) or 0.0
    return n / 100 if n > 1 else max(n, 0.0)


def url_canonica(no: dict) -> str:
    """Endereço estável do produto (identifica o item entre gerações e evita parâmetros de rastreio)."""
    shop, item = str(no.get("shopId") or "").strip(), str(no.get("itemId") or "").strip()
    if shop.isdigit() and item.isdigit():
        return f"https://shopee.com.br/product/{shop}/{item}"
    return str(no.get("productLink") or "").split("?")[0].strip()


def produto_de_no(no: dict) -> dict | None:
    """Um nó da resposta → produto no formato do motor. None se faltar o essencial (título, preço, link)."""
    titulo = re.sub(r"\s+", " ", str(no.get("productName") or "")).strip()
    preco = _preco(no.get("priceMin")) or _preco(no.get("price"))
    link = str(no.get("offerLink") or "").strip()
    url = url_canonica(no)
    if not (titulo and preco and url.startswith("http") and link.startswith("http")):
        return None
    vendas = _num(no.get("sales"))
    nota = _num(no.get("ratingStar"))
    imagem = str(no.get("imageUrl") or "").strip()
    return {
        "titulo": titulo, "preco": preco, "url": url, "link": link, "origem": "shopee",
        "imagem": imagem if imagem.startswith("http") else None,
        "vendas": int(vendas) if vendas and vendas > 0 else None,
        "nota": round(nota, 2) if nota and 0 < nota <= 5 else None,
        "comissao": _taxa(no.get("commissionRate")),
        "url_origem": str(no.get("productLink") or "").strip() or url,
    }


def pontuar(p: dict) -> float:
    """Quanto vale divulgar: vendas e nota pesam mais; a comissão dá só um empurrão (até +15%)."""
    nota = (p.get("nota") or 4.0) / 5
    return math.log1p(p.get("vendas") or 0) * (0.6 + 0.4 * nota) * (1 + min(p.get("comissao") or 0, 0.15))


def ranquear(produtos: list[dict], termo: str) -> list[dict]:
    """Tira repetidos e o que não tem relação com a busca (a Shopee devolve resultados frouxos) e ordena por pontuação."""
    toks = [t for t in cerebro.tokens(termo) if len(t) > 2] or cerebro.tokens(termo)
    vistos, saida = set(), []
    for p in produtos:
        if p["url"] in vistos:
            continue
        vistos.add(p["url"])
        titulo = cerebro.normalizar(p["titulo"])
        if toks and not any(t in titulo for t in toks):
            continue
        saida.append(p)
    return sorted(saida, key=lambda p: -pontuar(p))


# --------------------------------------------------------------------------- credenciais de cada pessoa
RE_APP_ID = re.compile(r"\d{5,20}")
RE_SECRET = re.compile(r"[A-Za-z0-9_\-+=/.]{16,200}")


def _linha(uid: int):
    return db.conn().execute("SELECT * FROM shopee_conexoes WHERE usuario_id=?", (uid,)).fetchone()


def credenciais(uid: int) -> tuple[str, str] | None:
    r = _linha(uid)
    if not r:
        return None
    secret = cofre.abrir(r["secret_cifrado"])
    return (r["app_id"], secret) if secret else None


def status(uid: int) -> dict:
    r = _linha(uid)
    if not r:
        return {"status": "desconectado", "mensagem": None, "app_id": None}
    return {"status": r["status"], "mensagem": r["mensagem"], "app_id": r["app_id"]}


def _gravar(uid: int, app_id: str, secret: str, estado: str, mensagem: str | None) -> None:
    c = db.conn()
    c.execute(
        "INSERT INTO shopee_conexoes (usuario_id, app_id, secret_cifrado, status, mensagem, atualizado) "
        "VALUES (?,?,?,?,?,datetime('now','localtime')) ON CONFLICT(usuario_id) DO UPDATE SET "
        "app_id=excluded.app_id, secret_cifrado=excluded.secret_cifrado, status=excluded.status, "
        "mensagem=excluded.mensagem, atualizado=excluded.atualizado",
        (uid, app_id, cofre.cifrar(secret), estado, mensagem))
    c.commit()


def _marcar_erro(uid: int, mensagem: str) -> None:
    c = db.conn()
    c.execute("UPDATE shopee_conexoes SET status='erro', mensagem=?, atualizado=datetime('now','localtime') "
              "WHERE usuario_id=?", (mensagem[:300], uid))
    c.commit()


def conectar(uid: int, app_id: str, secret: str) -> dict:
    """Valida as credenciais com uma consulta de verdade e só então guarda (Secret cifrado). ValueError com texto claro."""
    app_id, secret = (app_id or "").strip(), (secret or "").strip()
    if not RE_APP_ID.fullmatch(app_id):
        raise ValueError("O App ID da Shopee é um número (só dígitos). Copie de Portal do Afiliado > Open API.")
    if not RE_SECRET.fullmatch(secret):
        raise ValueError("O Secret parece incompleto. Copie o valor inteiro de Portal do Afiliado > Open API, sem espaços.")
    try:
        _chamar(app_id, secret, consulta_busca("celular", 1, ORDEM_RELEVANCIA, "itemId"))
    except ErroShopee as e:
        raise ValueError(str(e)) from e
    _gravar(uid, app_id, secret, "conectado", None)
    return status(uid)


def desconectar(uid: int) -> dict:
    c = db.conn()
    c.execute("DELETE FROM shopee_conexoes WHERE usuario_id=?", (uid,))
    c.commit()
    return status(uid)


def _exigir(uid: int) -> tuple[str, str]:
    cred = credenciais(uid)
    if not cred:
        raise ErroShopee("Conecte sua conta da Shopee primeiro, em Integrações.")
    return cred


# --------------------------------------------------------------------------- busca e links
def buscar(uid: int, termo: str, limite: int) -> list[dict]:
    """Produtos da Shopee para a busca, já ranqueados. Levanta ErroShopee. A conexão vira 'erro' se as credenciais caíram."""
    app_id, secret = _exigir(uid)
    pool = min(POR_BUSCA_MAX, max(limite * 2, 20))
    try:
        try:
            dados = _chamar(app_id, secret, consulta_busca(termo, pool, ORDEM_RELEVANCIA, CAMPOS_COMPLETOS))
        except ErroShopee as e:
            if e.codigo not in CODIGOS_CONSULTA:
                raise
            log.warning("Shopee recusou os campos completos (%s); tentando os básicos: %s", e.codigo, getattr(e, "detalhe", ""))
            dados = _chamar(app_id, secret, consulta_busca(termo, pool, ORDEM_RELEVANCIA, CAMPOS_BASICOS))
    except ErroShopee as e:
        if e.codigo in CODIGOS_CREDENCIAL:
            _marcar_erro(uid, str(e))
        raise
    nos = ((dados.get("productOfferV2") or {}).get("nodes")) or []
    produtos = [p for p in (produto_de_no(n) for n in nos if isinstance(n, dict)) if p]
    return ranquear(produtos, termo)[:limite]


def links_curtos(uid: int, urls: list[str], sub_ids: list[str]) -> dict[str, str]:
    """{url de origem: link curto de afiliado}. Falha em um lote não derruba os outros (volta sem esses links)."""
    app_id, secret = _exigir(uid)
    saida: dict[str, str] = {}
    for i in range(0, len(urls), LOTE_LINKS):
        lote = urls[i:i + LOTE_LINKS]
        try:
            dados = _chamar(app_id, secret, consulta_links(lote, sub_ids))
        except ErroShopee as e:
            if e.codigo in CODIGOS_CREDENCIAL:
                _marcar_erro(uid, str(e))
                raise
            log.warning("links curtos do lote %s falharam: %s", i // LOTE_LINKS + 1, e)
            continue
        for k, u in enumerate(lote):
            link = str(((dados.get(f"l{k}") or {}).get("shortLink")) or "").strip()
            if link.startswith("http"):
                saida[u] = link
    return saida


HOSTS_SHOPEE = ("shopee.com.br", "shopee.com", "shope.ee", "shp.ee")


def url_da_shopee(url: str) -> str:
    """Valida o endereço colado pela pessoa: precisa ser um link da Shopee (http/https)."""
    url = (url or "").strip()
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in HOSTS_SHOPEE):
        raise ValueError("Cole o endereço de um produto ou loja da Shopee (começa com https://shopee.com.br/…).")
    return url[:900]


def gerar_link(uid: int, url: str, sub_ids: list[str] | None = None) -> str:
    """Link curto de afiliado de qualquer endereço da Shopee (a opção "Link personalizado" do Portal do Afiliado)."""
    url = url_da_shopee(url)
    try:
        link = links_curtos(uid, [url], _sub_ids("thyfia", *(sub_ids or [])))
    except ErroShopee as e:
        raise ValueError(str(e)) from e
    if url not in link:
        raise ValueError("A Shopee não gerou o link para esse endereço. Confira se o produto ou a loja está disponível.")
    return link[url]


# --------------------------------------------------------------------------- geração (job)
def executar_job(job_id: str, uid: int, termo: str, limite: int) -> None:
    """Mesmo papel do motor do Mercado Livre, mas direto na API: busca, cria os links curtos e grava os itens."""
    from . import automacao, motor_ml  # import tardio: motor_ml carrega o navegador e não é preciso para o resto

    with _locks[uid]:
        resultados: list[dict] = []
        try:
            motor_ml._atualizar_job(job_id, status="coletando")
            produtos = buscar(uid, termo, limite)
            if not produtos:
                raise ErroShopee("A Shopee não achou produtos de afiliado para essa busca. Tente uma palavra-chave mais "
                                 "específica ou diferente.")
            motor_ml._atualizar_job(job_id, status="gerando", total=len(produtos), feitos=0, ok=0)

            # link curto com sub_ids (thyfia + a busca) para o relatório de conversões; se falhar, vale o link da própria busca
            curtos: dict[str, str] = {}
            try:
                curtos = links_curtos(uid, [p["url_origem"] for p in produtos], _sub_ids("thyfia", _slug(termo)))
            except ErroShopee as e:
                if e.codigo in CODIGOS_CREDENCIAL:
                    raise
            ok = 0
            for i, p in enumerate(produtos, 1):
                link = curtos.get(p["url_origem"]) or p["link"]
                gravou = itens.salvar_item(job_id, termo, p["titulo"], p["preco"], p["url"], link, imagem=p["imagem"],
                                           vendas=p["vendas"], nota=p["nota"])
                ok += bool(gravou)
                resultados.append({"url": p["url"], "titulo": p["titulo"], "preco": f"{p['preco']:.2f}".replace(".", ","),
                                   "ok": bool(gravou)})
                motor_ml._atualizar_job(job_id, feitos=i, ok=ok)

            cerebro.aprender_busca(termo, resultados)  # preços e vocabulário de nichos aprendem junto
            arquivo = motor_ml._salvar_planilha_local(job_id, uid, termo)
            auto = motor_ml._quer_auto_envio(uid) and itens.contar(job_id)[1] > 0
            motor_ml._atualizar_job(job_id, status="concluido", arquivo=arquivo, itens_importados=1,
                                    finalizado=datetime.now().isoformat(timespec="seconds"),
                                    **({"envio_status": "enviando", "envio_erro": None} if auto else {}))
            if auto:  # falha no envio fica registrada no envio, não derruba a geração
                try:
                    motor_ml.enviar_para_automacao(job_id, termo)
                except automacao.ErroAutomacao:
                    pass
        except ErroShopee as e:
            motor_ml._atualizar_job(job_id, status="erro", erro=str(e)[:300])
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            motor_ml._atualizar_job(job_id, status="erro", erro=str(e)[:300] or "Erro inesperado.")
