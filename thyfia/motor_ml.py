"""
Motor do Mercado Livre: conexão da conta de cada usuário e geração de links em segundo plano.

Cada usuário da ThyfIA tem seu próprio perfil de Chrome (dados/perfis/u<ID>), onde fica a
sessão do Mercado Livre. O login é feito pela própria pessoa numa janela do Chrome — a
ThyfIA nunca vê nem guarda a senha do Mercado Livre.
Durante a geração dos links o navegador roda fora da tela/minimizado; o usuário só vê
a animação de progresso na interface.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import logging
import traceback
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

import gerar_links_afiliado as ml  # funções de coleta/geração já testadas

from . import automacao, cerebro, db, itens, motor_busca

HUB_URL = ml.HUB_URL
TEMPO_LOGIN = 5 * 60  # segundos para a pessoa concluir o login
MOSTRAR_NAVEGADOR = os.environ.get("THYFIA_MOSTRAR_NAVEGADOR") == "1"  # debug
# Servidor sem tela (Docker/VPS): Chrome headless. O login no ML vem pela extensão (/api/ml/sessao).
HEADLESS = os.environ.get("THYFIA_HEADLESS") == "1"

_log = logging.getLogger("thyfia.motor_ml")
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


class SessaoExpirada(Exception):
    pass


# --------------------------------------------------------------------------- utilidades
def _perfil(uid: int):
    return db.PERFIS / f"u{uid}"


def _set_status_ml(uid: int, status: str, mensagem: str | None = None) -> None:
    c = db.conn()
    c.execute(
        "INSERT INTO ml_conexoes (usuario_id, status, mensagem, atualizado) "
        "VALUES (?,?,?,datetime('now','localtime')) ON CONFLICT(usuario_id) DO UPDATE SET "
        "status=excluded.status, mensagem=excluded.mensagem, atualizado=excluded.atualizado",
        (uid, status, mensagem),
    )
    c.commit()


SAMESITE = {"None", "Lax", "Strict"}


def _limpar_cookie(c: dict) -> dict | None:
    try:
        ck = {
            "name": str(c["name"]), "value": str(c["value"]),
            "domain": str(c["domain"]), "path": str(c.get("path") or "/"),
            "expires": float(c.get("expires", -1)),
            "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
            "sameSite": c.get("sameSite") if c.get("sameSite") in SAMESITE else "Lax",
        }
    except (KeyError, TypeError, ValueError):
        return None
    dominio = ck["domain"].lstrip(".")
    if not any(dominio.endswith(d) for d in ("mercadolivre.com.br", "mercadolivre.com", "mercadolibre.com")):
        return None
    if ck["sameSite"] == "None" and not ck["secure"]:
        ck["sameSite"] = "Lax"
    return ck


def salvar_sessao(uid: int, cookies: list[dict]) -> int:
    """Guarda a sessão do ML entregue pela extensão ThyfIA Conector."""
    limpos = [c for c in (_limpar_cookie(x) for x in cookies) if c]
    if not limpos:
        raise ValueError("Nenhum dado de sessão do Mercado Livre recebido. Faça login no Mercado Livre.")
    _set_status_ml(uid, "conectado", None)
    c = db.conn()
    c.execute("UPDATE ml_conexoes SET cookies=? WHERE usuario_id=?", (json.dumps(limpos), uid))
    c.commit()
    return len(limpos)


def _carregar_sessao(ctx, uid: int) -> None:
    row = db.conn().execute("SELECT cookies FROM ml_conexoes WHERE usuario_id=?", (uid,)).fetchone()
    if not row or not row["cookies"]:
        return
    cookies = json.loads(row["cookies"])
    try:
        ctx.add_cookies(cookies)
    except Exception:
        for ck in cookies:  # se algum cookie for recusado, aplica os demais
            try:
                ctx.add_cookies([ck])
            except Exception:
                pass


def status_ml(uid: int) -> dict:
    row = db.conn().execute("SELECT * FROM ml_conexoes WHERE usuario_id=?", (uid,)).fetchone()
    if not row:
        return {"status": "desconectado", "mensagem": None, "atualizado": None}
    return {"status": row["status"], "mensagem": row["mensagem"], "atualizado": row["atualizado"]}


def _caminho_chrome() -> str:
    if os.environ.get("THYFIA_CHROME"):
        return os.environ["THYFIA_CHROME"]
    if sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if sys.platform.startswith("win"):
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    return shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def navegador(uid: int, visivel: bool):
    """Abre o Google Chrome NORMAL (sem flags de automação, para o Google/ML não bloquearem
    o login) com o perfil do usuário e conecta a ThyfIA nele via porta de depuração."""
    oculto = not (visivel or MOSTRAR_NAVEGADOR)
    porta = _porta_livre()
    # não deixa o Chrome restaurar as abas das execuções anteriores (elas se acumulavam)
    for pasta in ("Sessions", "Session Storage"):
        shutil.rmtree(_perfil(uid) / "Default" / pasta, ignore_errors=True)
    for arq in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        try:
            (_perfil(uid) / "Default" / arq).unlink()
        except OSError:
            pass
    # Trava de perfil deixada por um Chrome morto (ex.: container recriado no deploy → outro hostname):
    # sem apagar, o Chrome novo recusa o perfil e fecha na hora. Aqui só há um Chrome por usuário (lock).
    for arq in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (_perfil(uid) / arq).unlink()
        except OSError:
            pass
    args = [
        _caminho_chrome(),
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={_perfil(uid)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=pt-BR",
    ]
    if HEADLESS:
        args += ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                 "--window-size=1280,800"]
    else:
        args += ["--window-position=-2400,-2400", "--window-size=1280,800"] if oculto \
            else ["--window-size=1100,820", "--new-window"]
    args.append(HUB_URL if not oculto else "about:blank")
    _perfil(uid).mkdir(parents=True, exist_ok=True)
    erro_chrome = tempfile.TemporaryFile()  # guarda o que o Chrome disser, para explicar uma falha
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=erro_chrome)

    # espera a porta de depuração responder
    fim = time.time() + 20
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/version", timeout=1)
            break
        except Exception:
            if proc.poll() is not None or time.time() > fim:
                proc.kill()
                erro_chrome.seek(0)
                detalhe = erro_chrome.read()[-400:].decode("utf-8", "replace").strip()
                _log.error("Chrome não abriu (saída=%s): %s", proc.poll(), detalhe or "(sem mensagem)")
                dica = f" Detalhe: {detalhe.splitlines()[-1][:160]}" if detalhe else ""
                raise RuntimeError("Não consegui abrir o Chrome do servidor. Tente novamente em instantes; "
                                   "se persistir, avise o suporte." + dica)
            time.sleep(0.3)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{porta}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for extra in ctx.pages[1:]:  # garante uma única aba
            try:
                extra.close()
            except Exception:
                pass
        if oculto:
            try:  # minimiza a janela para não aparecer para o usuário
                cdp = ctx.new_cdp_session(page)
                win = cdp.send("Browser.getWindowForTarget")
                cdp.send("Browser.setWindowBounds",
                         {"windowId": win["windowId"], "bounds": {"windowState": "minimized"}})
            except Exception:
                pass
        else:
            page.bring_to_front()
        yield ctx, page
    finally:
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        pw.stop()


def _esta_logado(url: str) -> bool:
    return "/afiliados" in url and not any(x in url for x in ("login", "registration", "jms/"))


# --------------------------------------------------------------------------- conexão
def conectar(uid: int) -> None:
    """Abre uma janela do Chrome para a pessoa logar no ML; detecta sozinho quando terminar."""
    if HEADLESS:
        _set_status_ml(uid, "erro", "Este servidor não tem tela. Conecte pela extensão ThyfIA Conector "
                                    "(botão de sincronizar) no seu navegador.")
        return
    lock = _locks[uid]
    if not lock.acquire(blocking=False):
        _set_status_ml(uid, "erro", "Já existe uma operação em andamento para sua conta.")
        return
    _set_status_ml(uid, "conectando", "Aguardando login no Mercado Livre…")
    try:
        with navegador(uid, visivel=True) as (ctx, page):
            # não navegamos nem mexemos na página: a pessoa faz o login livremente
            fim = time.time() + TEMPO_LOGIN
            while time.time() < fim:
                paginas = ctx.pages
                if not paginas:
                    _set_status_ml(uid, "erro", "A janela do Mercado Livre foi fechada antes do login.")
                    return
                # wait_for_timeout (e não time.sleep) para o Playwright processar os eventos
                # de navegação — senão a URL da página fica "congelada".
                paginas[0].wait_for_timeout(2000)
                paginas = ctx.pages
                if any(_esta_logado(p.url) for p in paginas):
                    time.sleep(1.5)  # deixa os cookies da sessão gravarem no perfil
                    _set_status_ml(uid, "conectado", None)
                    return
            _set_status_ml(uid, "erro", "Tempo esgotado. Tente conectar novamente.")
    except Exception as e:  # janela fechada pelo usuário, Chrome ausente etc.
        if status_ml(uid)["status"] != "conectado":
            texto = str(e).lower()
            msg = "A janela do Mercado Livre foi fechada antes do login." \
                if any(x in texto for x in ("closed", "disconnected", "target")) else str(e)
            _set_status_ml(uid, "erro", msg)
    finally:
        lock.release()


def desconectar(uid: int) -> None:
    with _locks[uid]:
        shutil.rmtree(_perfil(uid), ignore_errors=True)
        _set_status_ml(uid, "desconectado", None)
        c = db.conn()
        c.execute("UPDATE ml_conexoes SET cookies=NULL WHERE usuario_id=?", (uid,))
        c.commit()


# --------------------------------------------------------------------------- jobs
def _atualizar_job(job_id: str, **campos) -> None:
    sets = ", ".join(f"{k}=?" for k in campos)
    c = db.conn()
    c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*campos.values(), job_id))
    c.commit()


def _salvar_planilha_local(job_id: str, uid: int, termo: str) -> str | None:
    """Cópia local OPCIONAL da planilha (THYFIA_SALVAR_PLANILHA=0 desliga). Os dados ficam no
    banco; se gravar o arquivo falhar, a geração segue normalmente."""
    if not itens.salvar_planilha_local():
        return None
    try:
        pasta = db.PLANILHAS / f"u{uid}"
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / f"{job_id}.xlsx"
        arquivo.write_bytes(itens.gerar_planilha(job_id, termo))
        return str(arquivo)
    except OSError:
        traceback.print_exc()
        return None


def _quer_auto_envio(uid: int) -> bool:
    """Só envia sozinho se o cliente ligou a opção E o plano (Pro+) continua valendo."""
    r = db.conn().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not (r and r["auto_enviar"]):
        return False
    from . import planos  # import local (evita ciclo)
    return planos.tem_recurso(r, "automacao")


def _cliente_do_job(job_id: str) -> dict | None:
    """Dados do dono da geração (WhatsApp/Telegram) para o n8n saber para quem divulgar."""
    from . import tenant  # import local: tenant depende deste módulo
    r = db.conn().execute("SELECT usuario_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    return tenant.cliente_payload(r["usuario_id"]) if r else None


def enviar_para_automacao(job_id: str, termo: str) -> dict:
    """Envia ao n8n os links da geração LIDOS DO BANCO e registra o resultado no histórico.
    Sem links no banco → ErroAutomacao('vazio'), sem chamar o n8n nem mexer no status."""
    linhas = itens.para_envio(itens.itens_do_job(job_id))
    if not automacao.montar_links(linhas):
        raise automacao.ErroAutomacao(automacao.SEM_LINKS, "vazio")
    _atualizar_job(job_id, envio_status="enviando", envio_erro=None)
    agora = lambda: datetime.now().isoformat(sep=" ", timespec="seconds")
    try:
        resumo = automacao.enviar_itens(linhas, termo, cliente=_cliente_do_job(job_id))
    except automacao.ErroAutomacao as e:
        _atualizar_job(job_id, envio_status="erro", envio_erro=str(e), envio_em=agora())
        raise
    except Exception:
        traceback.print_exc()
        _atualizar_job(job_id, envio_status="erro", envio_erro="Erro inesperado no envio.", envio_em=agora())
        raise automacao.ErroAutomacao("Erro inesperado no envio.", "n8n")
    _atualizar_job(job_id, envio_status="enviado", envio_erro=None, envio_em=agora(),
                   envio_resumo=json.dumps(resumo, ensure_ascii=False))
    return resumo


def executar_job(job_id: str, uid: int, termo: str, limite: int, trechos: str | None = None) -> None:
    lock = _locks[uid]
    with lock:
        resultados: list[dict] = []
        try:
            with navegador(uid, visivel=False) as (ctx, page):
                _carregar_sessao(ctx, uid)  # sessão do navegador da pessoa (extensão)
                capturados: list[str] = []
                ml.escutar_rede(page, capturados)

                # tudo acontece numa única aba, parada no Gerador de Links
                page.goto(ml.LINKBUILDER_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if not _esta_logado(page.url):
                    raise SessaoExpirada()

                # 1) coleta (motor de busca dentro do linkbuilder) + cérebro
                _atualizar_job(job_id, status="coletando")
                try:
                    # 1ª opção: produtos lidos pela extensão no navegador da pessoa; senão o servidor busca
                    produtos = motor_busca.extrair_produtos(trechos) if trechos else []
                    if not produtos:
                        produtos = motor_busca.buscar(page, termo, limite + 10)
                except motor_busca.BuscaBloqueada:
                    # sem resultados ao vivo: usa o que a ThyfIA já conhece, se houver
                    produtos = cerebro.recuperar(termo, limite)
                    if not produtos:
                        raise
                if not _esta_logado(page.url):
                    raise SessaoExpirada()
                produtos = cerebro.ranquear(produtos)
                if len(produtos) < limite:  # completa com o que a ThyfIA já conhece
                    produtos += cerebro.recuperar(termo, limite - len(produtos),
                                                  excluir={p["url"] for p in produtos})
                produtos = produtos[:limite]
                if not produtos:
                    raise RuntimeError("Nenhum produto encontrado para essa busca.")
                _atualizar_job(job_id, status="gerando", total=len(produtos))

                # 2) geração dos links
                ok = 0
                for i, prod in enumerate(produtos, 1):
                    # (não abre a página do produto: o título já vem da busca/cérebro)
                    try:
                        link = ml.gerar_link(page, prod["url"], capturados)
                    except PWTimeout:
                        link = None
                    except Exception:  # erro pontual num produto não derruba o job
                        traceback.print_exc()
                        link = None
                        try:
                            page.goto(ml.LINKBUILDER_URL, wait_until="domcontentloaded")
                        except Exception:
                            pass
                    if not link and "login" in page.url:
                        raise SessaoExpirada()
                    ok += bool(link)
                    resultados.append({**prod, "ok": bool(link)})
                    # grava o item no banco assim que o link é criado (fonte dos dados)
                    itens.salvar_item(job_id, termo, prod.get("titulo", ""), prod.get("preco"),
                                      prod["url"], link, imagem=prod.get("imagem"))
                    _atualizar_job(job_id, feitos=i, ok=ok)
                    page.wait_for_timeout(600)

            # 3) aprendizado + cópia local opcional da planilha (os dados já estão no banco)
            cerebro.aprender_busca(termo, resultados)
            arquivo = _salvar_planilha_local(job_id, uid, termo)
            auto = _quer_auto_envio(uid) and itens.contar(job_id)[1] > 0
            _atualizar_job(job_id, status="concluido", arquivo=arquivo, itens_importados=1,
                           finalizado=datetime.now().isoformat(timespec="seconds"),
                           **({"envio_status": "enviando", "envio_erro": None} if auto else {}))
            if auto:  # falha no envio fica registrada no envio, não derruba a geração
                try:
                    enviar_para_automacao(job_id, termo)
                except automacao.ErroAutomacao:
                    pass

        except motor_busca.BuscaBloqueada as e:
            _atualizar_job(job_id, status="erro", erro=str(e))
        except SessaoExpirada:
            _set_status_ml(uid, "desconectado", "Sua sessão do Mercado Livre expirou.")
            _atualizar_job(job_id, status="erro",
                           erro="Sua sessão do Mercado Livre expirou. Conecte sua conta novamente.")
        except Exception as e:
            traceback.print_exc()
            if resultados:  # aproveita o que já foi feito
                cerebro.aprender_busca(termo, resultados)
            _atualizar_job(job_id, status="erro", erro=str(e)[:300] or "Erro inesperado.")
