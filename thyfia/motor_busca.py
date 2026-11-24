"""
Motor de busca da ThyfIA — procura produtos SEM sair da aba do Gerador de Links.

Por que assim: o Mercado Livre bloqueia (captcha / "account-verification") buscas feitas
por requisições soltas, mesmo com a sessão logada. Só uma página renderizada pelo próprio
Chrome passa pela verificação. Então o motor abre a busca num <iframe> invisível DENTRO da
página do linkbuilder: a aba continua no gerador, nenhuma aba nova é aberta, e os produtos
são lidos do JSON que o ML embute na página de resultados (mais estável que seletores CSS).
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from playwright.sync_api import Page

LINKBUILDER_URL = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
_log = logging.getLogger("thyfia.motor_busca")
NOME_FRAME = "thyfia-motor"
POR_PAGINA = 48
MAX_PAGINAS = 4
BLOQUEIOS = ("captcha", "account-verification", "web-risk-verification", "/jms/", "login")


class BuscaBloqueada(Exception):
    """O Mercado Livre pediu verificação (captcha) ou login na busca."""


# --------------------------------------------------------------------------- parse
def _desescapar(html: str) -> str:
    # o estado vem como JSON dentro de string JS (às vezes escapado duas vezes)
    t = html
    for _ in range(2):
        t = t.replace('\\\\"', '"').replace('\\"', '"')
    return t.replace("\\\\u002F", "/").replace("\\u002F", "/")


def _texto_json(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


def _preco_br(valor: str | None) -> str:
    if not valor:
        return ""
    try:
        return f"{float(valor):.2f}".replace(".", ",")
    except ValueError:
        return ""


RE_URL = re.compile(r'"metadata":\{"id":"(MLB\d+)".*?"url":"([^"]+)"', re.S)
RE_TITULO = re.compile(r'"title":\{"text":"((?:[^"\\]|\\.)*)"')
RE_PRECO = re.compile(r'"prc":([\d.]+)')
RE_PRECO_ATUAL = re.compile(r'"current_price":\{"value":([\d.]+)')  # cartões sem "prc" trazem o preço aqui
RE_VENDAS = re.compile(r'"sales":(\d+)')
RE_NOTA = re.compile(r'"reviews_rating":([\d.]+)')
# selo do cartão (etiqueta na foto): {"highlight":{"id":"best_seller","text":"MAIS VENDIDO"…
RE_SELO = re.compile(r'"highlight":\{"id":"[a-z_]+","text":"((?:[^"\\]|\\.)*)"')
# preço "de" (riscado) e % de desconto que o próprio Mercado Livre mostra
RE_PRECO_DE = re.compile(r'"key":"previous_price".{0,300}?"price":\{"value":([\d.]+)', re.S)
RE_DESCONTO = re.compile(r'"discount_pct":(\d+)')
# o que o preço exige: "com Cupom" (só vale com o cupom ativado) ou "no Pix" (só vale pagando no Pix)
RE_CONDICAO = re.compile(r'"discount_complement":\{.{0,400}?"label":\{"text":"((?:[^"\\]|\\.)*)"', re.S)
# id de foto do ML: "608923-MLB120463575227_102026" (formato inconfundível, diferente do id do item)
RE_FOTO = re.compile(r'"id":"(\d{3,7}-ML[A-Z]\d+_\d{6})"')
CDN_FOTO = "https://http2.mlstatic.com/D_NQ_NP_2X_{}-F.jpg"  # JPEG em alta (o Instagram exige JPEG)


def _condicao(texto: str) -> str | None:
    t = re.sub(r"[^a-z]", "", texto.lower())
    return "cupom" if "cupom" in t else "pix" if "pix" in t else None


def _dados_de_oferta(bloco: str, preco_atual: float | None) -> dict:
    """Selo, preço "de", desconto e condição do preço, lidos do cartão. Tudo opcional: o que não vier fica None."""
    selo = RE_SELO.search(bloco)
    de = RE_PRECO_DE.search(bloco)
    desc = RE_DESCONTO.search(bloco)
    cond = RE_CONDICAO.search(bloco)
    preco_de = float(de.group(1)) if de else None
    if preco_de is not None and (not preco_atual or preco_de <= preco_atual * 1.03):
        preco_de = None  # "de" que não é maior que o preço atual não serve de nada
    pct = int(desc.group(1)) if desc else None
    return {
        "selo": _texto_json(selo.group(1)).strip().upper() or None if selo else None,
        "preco_de": preco_de,
        "desconto_pct": pct if preco_de and pct and 1 <= pct <= 95 else None,
        "condicao": _condicao(_texto_json(cond.group(1))) if cond else None,
    }


def extrair_produtos(html: str) -> list[dict]:
    """Lê os cards (polycards) do JSON embutido na página de resultados."""
    t = _desescapar(html)
    partes = t.split('"polycard":{')[1:]
    produtos, vistos = [], set()
    for bloco in partes:
        m = RE_URL.search(bloco[:4000])
        if not m:
            continue
        item_id, url = m.groups()
        url = url.split("#")[0].split("?")[0]
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        if "mercadolivre.com.br" not in url or "click1." in url or url in vistos:
            continue
        vistos.add(url)
        tit = RE_TITULO.search(bloco)
        prc = RE_PRECO.search(bloco[:4000]) or RE_PRECO_ATUAL.search(bloco)
        vendas = RE_VENDAS.search(bloco[:4000])
        nota = RE_NOTA.search(bloco[:4000])
        foto = RE_FOTO.search(bloco)
        try:
            atual = float(prc.group(1)) if prc else None
        except ValueError:
            atual = None
        produtos.append({
            **_dados_de_oferta(bloco[:7000], atual),
            "titulo": _texto_json(tit.group(1)).strip() if tit else "",
            "preco": _preco_br(prc.group(1) if prc else None),
            "url": url,
            "item_id": item_id,
            "vendas": int(vendas.group(1)) if vendas else 0,
            "nota": float(nota.group(1)) if nota else 0.0,
            "imagem": CDN_FOTO.format(foto.group(1)) if foto else None,
            "origem": "cartao",
        })
    return produtos


JS_DOM = """() => {
  const out = [];
  document.querySelectorAll('li.ui-search-layout__item, div.poly-card, div.ui-search-result__wrapper').forEach(c => {
    const a = c.querySelector('a.poly-component__title, h3 a, h2 a, a.ui-search-link, a[href*="MLB"]');
    if (!a) return;
    const p = c.querySelector('.poly-price__current .andes-money-amount__fraction, .andes-money-amount__fraction');
    const ct = c.querySelector('.poly-price__current .andes-money-amount__cents');
    out.push({titulo: (a.innerText || a.title || '').trim(), url: a.href,
              preco: p ? p.innerText.trim() + (ct ? ',' + ct.innerText.trim() : '') : ''});
  });
  return out;
}"""


# --------------------------------------------------------------------------- iframe
def _url_busca(termo: str, pagina: int) -> str:
    slug = quote(re.sub(r"\s+", "-", termo.strip()))
    if pagina == 0:
        return f"https://lista.mercadolivre.com.br/{slug}"
    return f"https://lista.mercadolivre.com.br/{slug}_Desde_{pagina * POR_PAGINA + 1}_NoIndex_True"


def _carregar_no_frame(page: Page, url: str, timeout_ms: int = 30000):
    """Aponta o iframe invisível para `url` e devolve o Frame do Playwright já carregado."""
    page.evaluate(
        """([nome, url]) => {
          // recria a cada página: assim nunca lemos o documento da página anterior
          document.querySelector(`iframe[name="${nome}"]`)?.remove();
          const f = document.createElement('iframe');
          f.name = nome;
          f.setAttribute('aria-hidden', 'true');
          f.style.cssText = 'position:fixed;left:-6000px;top:0;width:1280px;height:900px;border:0;opacity:0;pointer-events:none';
          f.src = url;
          document.body.appendChild(f);
        }""",
        [NOME_FRAME, url],
    )
    fim = page.evaluate("Date.now()") + timeout_ms
    while True:
        fr = page.frame(name=NOME_FRAME)
        if fr and fr.url not in ("", "about:blank") and fr.url.split("?")[0] != "about:blank":
            try:
                fr.wait_for_load_state("domcontentloaded", timeout=5000)
                # espera o estado/cards aparecerem (página é renderizada em streaming)
                fr.wait_for_function(
                    "() => document.readyState === 'complete' || "
                    "document.documentElement.innerHTML.includes('polycard')",
                    timeout=10000,
                )
                return fr
            except Exception:
                pass
        if page.evaluate("Date.now()") > fim:
            raise TimeoutError("A busca do Mercado Livre demorou demais para responder.")
        page.wait_for_timeout(400)


def _remover_frame(page: Page) -> None:
    try:
        page.evaluate(f"() => document.querySelector('iframe[name=\"{NOME_FRAME}\"]')?.remove()")
    except Exception:
        pass


def buscar(page: Page, termo: str, limite: int) -> list[dict]:
    """Busca `termo` no ML usando a aba atual (que fica no linkbuilder) e devolve até `limite`
    produtos [{titulo, preco, url, item_id, vendas, nota}]."""
    if "/afiliados/linkbuilder" not in page.url:
        page.goto(LINKBUILDER_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

    produtos: list[dict] = []
    vistos: set[str] = set()
    try:
        for pagina in range(MAX_PAGINAS):
            fr = _carregar_no_frame(page, _url_busca(termo, pagina))
            if any(b in fr.url for b in BLOQUEIOS):
                if produtos:
                    break  # já temos algo; usa o que veio
                raise BuscaBloqueada(
                    "O Mercado Livre pediu uma verificação de segurança na busca. "
                    "Abra o Mercado Livre no seu Chrome, faça uma busca qualquer e tente de novo."
                )
            html = fr.content()
            # página pequena e sem cards = ainda carregando/redirecionando (ex.: etapa intermediária do
            # site): dá até 15 s para os resultados aparecerem antes de desistir
            for _ in range(15):
                if "polycard" in html or any(b in fr.url for b in BLOQUEIOS):
                    break
                page.wait_for_timeout(1000)
                try:
                    fr = page.frame(name=NOME_FRAME) or fr
                    html = fr.content()
                except Exception:
                    pass
            if any(b in fr.url for b in BLOQUEIOS) and not produtos:
                raise BuscaBloqueada(
                    "O Mercado Livre pediu uma verificação de segurança na busca. "
                    "Abra o Mercado Livre no seu Chrome, faça uma busca qualquer e tente de novo."
                )
            novos = extrair_produtos(html)
            if not novos:  # fallback: lê os cards renderizados
                try:
                    novos = [
                        {**p, "url": p["url"].split("#")[0].split("?")[0]}
                        for p in fr.evaluate(JS_DOM)
                        if "click1." not in p["url"]
                    ]
                except Exception:
                    novos = []
            if not novos:  # deixa no log o que o Mercado Livre devolveu, para diagnosticar
                try:
                    titulo = fr.title()
                except Exception:
                    titulo = "?"
                baixo = html.lower()
                trecho = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)).strip()[:300]
                _log.warning("Busca '%s' pág.%s sem produtos: url=%s título=%r html=%d bytes polycard=%s "
                             "captcha=%s login=%s texto=%r", termo, pagina, fr.url[:120], titulo[:80], len(html),
                             "polycard" in baixo, "captcha" in baixo, "login" in baixo[:3000], trecho)
            antes = len(produtos)
            for p in novos:
                if p["url"] not in vistos:
                    vistos.add(p["url"])
                    produtos.append(p)
            if len(produtos) >= limite or len(produtos) == antes:
                break  # chegou no limite ou a página não trouxe nada novo
    finally:
        _remover_frame(page)
    return produtos[:limite]
