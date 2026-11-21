"""
Automação: gera links de afiliado do Mercado Livre e salva numa planilha Excel.

Como funciona
-------------
1. Conecta no SEU Chrome (já logado na conta de afiliado) via porta de depuração 9222.
   Se não achar o Chrome nessa porta, abre um Chrome próprio com um perfil salvo em
   ./perfil_chrome (você faz login uma vez e ele lembra).
2. Coleta os produtos:
     - de uma busca no Mercado Livre  (--busca "fone bluetooth" --limite 20), ou
     - de um arquivo de texto com uma URL por linha (--arquivo produtos.txt)
3. Abre o Gerador de Links do painel de afiliados, gera o link de cada produto
   (formato https://mercadolivre.com/sec/XXXX) e captura o resultado.
4. Salva em links_afiliado.xlsx (acrescenta linhas, sem duplicar produtos).

Uso
---
    python gerar_links_afiliado.py --busca "air fryer" --limite 10
    python gerar_links_afiliado.py --arquivo produtos.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

PASTA = Path(__file__).resolve().parent
PLANILHA = PASTA / "links_afiliado.xlsx"
PERFIL = PASTA / "perfil_chrome"
CDP_URL = "http://localhost:9222"

LINKBUILDER_URL = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
HUB_URL = "https://www.mercadolivre.com.br/afiliados/hub?is_affiliate=true"

# Link curto de afiliado gerado pelo ML (formato atual: https://meli.la/XXXX; antigo: /sec/XXXX)
RE_LINK_AFILIADO = re.compile(
    r"https?://(?:meli\.la/[A-Za-z0-9]+|(?:www\.)?mercadolivre\.com(?:\.br)?/sec/[A-Za-z0-9]+)"
)
COLUNAS = ["Data/Hora", "Título", "Preço (R$)", "URL do Produto", "Link de Afiliado"]


# --------------------------------------------------------------------------- navegador
def abrir_contexto(pw) -> tuple[BrowserContext, bool]:
    """Tenta usar o Chrome já aberto (CDP). Senão abre um Chrome com perfil persistente.
    Retorna (contexto, conectado_ao_chrome_existente)."""
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
        print(f"✔ Conectado ao Chrome já aberto ({CDP_URL})")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        return ctx, True
    except Exception:
        print("ℹ Chrome com depuração não encontrado na porta 9222 — abrindo Chrome próprio.")
        ctx = pw.chromium.launch_persistent_context(
            str(PERFIL),
            channel="chrome",  # usa o Google Chrome instalado
            headless=False,
            viewport={"width": 1366, "height": 850},
            locale="pt-BR",
        )
        return ctx, False


def garantir_login(page: Page) -> None:
    page.goto(HUB_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if "login" in page.url or "registration" in page.url:
        print("\n⚠ Você não está logado. Faça login no Mercado Livre na janela do Chrome.")
        input("  Depois de logar e ver o painel de afiliados, aperte ENTER aqui... ")
        page.goto(HUB_URL, wait_until="domcontentloaded")


# --------------------------------------------------------------------------- coleta
def limpar_url(url: str) -> str:
    url = url.split("#")[0]
    # remove parâmetros de rastreio, mantendo a URL canônica do produto
    return url.split("?")[0]


def coletar_da_busca(page: Page, termo: str, limite: int) -> list[dict]:
    slug = quote(termo.strip().replace(" ", "-"))
    url = f"https://lista.mercadolivre.com.br/{slug}"
    print(f"🔎 Buscando: {termo}  →  {url}")
    produtos: list[dict] = []
    vistos: set[str] = set()

    while url and len(produtos) < limite:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        itens = page.evaluate(
            """() => {
              const cards = document.querySelectorAll(
                'li.ui-search-layout__item, div.poly-card, div.ui-search-result__wrapper');
              const out = [];
              cards.forEach(c => {
                const a = c.querySelector('a.poly-component__title, h2 a, a.ui-search-link, a[href*="MLB"]');
                if (!a) return;
                const preco = c.querySelector('.poly-price__current .andes-money-amount__fraction, .andes-money-amount__fraction');
                const cent = c.querySelector('.poly-price__current .andes-money-amount__cents');
                out.push({
                  titulo: (a.innerText || a.title || '').trim(),
                  url: a.href,
                  preco: preco ? preco.innerText.trim() + (cent ? ',' + cent.innerText.trim() : '') : ''
                });
              });
              const prox = document.querySelector('li.andes-pagination__button--next a');
              return {out, prox: prox ? prox.href : null};
            }"""
        )
        for it in itens["out"]:
            if "click1.mercadolivre" in it["url"]:  # anúncio patrocinado com redirect
                continue
            u = limpar_url(it["url"])
            if u in vistos or "mercadolivre.com.br" not in u:
                continue
            vistos.add(u)
            produtos.append({"titulo": it["titulo"], "preco": it["preco"], "url": u})
            if len(produtos) >= limite:
                break
        url = itens["prox"]

    print(f"   {len(produtos)} produtos coletados.")
    return produtos


def coletar_do_arquivo(caminho: Path) -> list[dict]:
    linhas = [l.strip() for l in caminho.read_text(encoding="utf-8").splitlines()]
    urls = [l for l in linhas if l.startswith("http")]
    print(f"📄 {len(urls)} URLs lidas de {caminho.name}")
    return [{"titulo": "", "preco": "", "url": limpar_url(u)} for u in urls]


def preencher_titulo(page: Page, produto: dict) -> None:
    """Para produtos vindos do arquivo: pega título e preço na página do produto."""
    try:
        page.goto(produto["url"], wait_until="domcontentloaded", timeout=30000)
        dados = page.evaluate(
            """() => ({
              titulo: (document.querySelector('h1.ui-pdp-title, h1')||{}).innerText || document.title,
              preco: (document.querySelector('.ui-pdp-price__second-line .andes-money-amount__fraction')||{}).innerText || ''
            })"""
        )
        produto["titulo"] = (dados["titulo"] or "").strip()
        produto["preco"] = (dados["preco"] or "").strip()
    except PWTimeout:
        pass


# --------------------------------------------------------------------------- gerador de links
def links_na_tela(page: Page) -> list[str]:
    """Procura links /sec/ no texto da página e dentro de inputs/textareas."""
    texto = page.evaluate(
        """() => {
          let t = document.body.innerText;
          document.querySelectorAll('input, textarea').forEach(e => t += '\\n' + (e.value || ''));
          document.querySelectorAll('a[href*="/sec/"], a[href*="meli.la"]').forEach(a => t += '\\n' + a.href);
          return t;
        }"""
    )
    return RE_LINK_AFILIADO.findall(texto)


def gerar_link(page: Page, url_produto: str, capturados_rede: list[str], timeout_s: int = 20) -> str | None:
    if "/afiliados/linkbuilder" not in page.url:
        page.goto(LINKBUILDER_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    antes = set(links_na_tela(page))
    capturados_rede.clear()

    campo = page.locator("textarea, input[type='text'][placeholder*='link' i], input[type='url']").first
    campo.wait_for(state="visible", timeout=15000)
    campo.click()
    campo.fill("")
    campo.fill(url_produto)

    botao = page.get_by_role("button", name=re.compile(r"gerar", re.I)).first
    botao.click()

    fim = time.time() + timeout_s
    while time.time() < fim:
        # 1º: resposta da API capturada na rede (mais confiável)
        if capturados_rede:
            return capturados_rede[-1]
        # 2º: link novo apareceu na tela
        novos = [l for l in links_na_tela(page) if l not in antes]
        if novos:
            return novos[-1]
        page.wait_for_timeout(500)
    return None


def escutar_rede(page: Page, destino: list[str]) -> None:
    def ao_responder(resp):
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            if "affiliate" not in resp.url and "afiliados" not in resp.url and "link" not in resp.url:
                return
            corpo = resp.text()
            achados = RE_LINK_AFILIADO.findall(corpo.replace("\\/", "/"))
            if achados:
                destino.extend(achados)
        except Exception:
            pass

    page.on("response", ao_responder)


# --------------------------------------------------------------------------- planilha
def abrir_planilha():
    if PLANILHA.exists():
        wb = load_workbook(PLANILHA)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Links Afiliado"
        ws.append(COLUNAS)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2D3277")
        for col, larg in zip("ABCDE", (18, 60, 12, 70, 40)):
            ws.column_dimensions[col].width = larg
        ws.freeze_panes = "A2"
    # remove linhas com ERRO para que esses produtos sejam tentados de novo
    for i in range(ws.max_row, 1, -1):
        if ws.cell(row=i, column=5).value in (None, "", "ERRO"):
            ws.delete_rows(i)
    ja_salvos = {row[3] for row in ws.iter_rows(min_row=2, values_only=True) if row[3]}
    return wb, ws, ja_salvos


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Gera links de afiliado do Mercado Livre")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--busca", help='termo de busca, ex: "fone bluetooth"')
    grupo.add_argument("--arquivo", type=Path, help="arquivo .txt com uma URL de produto por linha")
    ap.add_argument("--limite", type=int, default=20, help="máx. de produtos da busca (padrão 20)")
    args = ap.parse_args()

    wb, ws, ja_salvos = abrir_planilha()

    with sync_playwright() as pw:
        ctx, existente = abrir_contexto(pw)
        page = ctx.new_page()
        capturados: list[str] = []
        escutar_rede(page, capturados)

        garantir_login(page)

        if args.busca:
            produtos = coletar_da_busca(page, args.busca, args.limite)
        else:
            produtos = coletar_do_arquivo(args.arquivo)

        produtos = [p for p in produtos if p["url"] not in ja_salvos]
        if not produtos:
            print("Nenhum produto novo para gerar link.")
        total_ok = 0

        for i, prod in enumerate(produtos, 1):
            if not prod["titulo"]:
                preencher_titulo(page, prod)
            print(f"[{i}/{len(produtos)}] {prod['titulo'][:60] or prod['url']}")
            try:
                link = gerar_link(page, prod["url"], capturados)
            except PWTimeout:
                link = None
                print("   ✖ Não encontrei o campo/botão do Gerador de Links.")
            if link:
                print(f"   ✔ {link}")
                total_ok += 1
            else:
                print("   ✖ Link não gerado (verifique se o produto é elegível ao programa).")

            ws.append([
                datetime.now().strftime("%d/%m/%Y %H:%M"),
                prod["titulo"],
                prod["preco"],
                prod["url"],
                link or "ERRO",
            ])
            wb.save(PLANILHA)  # salva a cada item para não perder nada
            page.wait_for_timeout(800)

        page.close()
        if not existente:
            ctx.close()

    print(f"\n✅ {total_ok}/{len(produtos)} links gerados. Planilha: {PLANILHA}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrompido pelo usuário.")
