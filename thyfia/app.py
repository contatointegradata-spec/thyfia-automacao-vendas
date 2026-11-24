"""API e servidor web da ThyfIA.

Rodar:  .venv/bin/python -m uvicorn thyfia.app:app --port 8000
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import threading
import time
import zipfile
from pathlib import Path

from dotenv import load_dotenv

# configuração local (N8N_WEBHOOK_URL, N8N_API_KEY…) — o .env fica fora do git
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (aprendizado, piloto, agente_publicacao, auth, automacao, cerebro, configuracoes, db, facebook, imagem_host, instagram, emails,
               itens, kiwify, motor_ml, nichos, planos, shopee, tenant, video)

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "thyfia_sessao"
ATIVOS = ("iniciando", "coletando", "gerando")

db.init()
auth.garantir_admin(inicial=True)
# jobs que estavam rodando quando o servidor caiu/reiniciou nunca vão terminar: libera o usuário
_c = db.conn()
_c.execute(
    f"UPDATE jobs SET status='erro', erro='A geração foi interrompida (servidor reiniciado). Tente de novo.' "
    f"WHERE status IN ({','.join('?' * len(ATIVOS))})", ATIVOS)
_c.execute("UPDATE ml_conexoes SET status='erro', mensagem='Conexão interrompida. Tente novamente.' "
           "WHERE status='conectando'")
_c.commit()

# avisos da ThyfIA (importação/limpeza) aparecem no terminal do servidor
_log = logging.getLogger("thyfia")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     [thyfia] %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
# gerações antigas que só tinham planilha local → itens no banco (lê cada planilha uma vez)
try:
    itens.importar_antigas()
    itens.limpar_planilhas()  # opcional: THYFIA_LIMPAR_PLANILHAS_DIAS (padrão desligado)
except Exception:  # nunca impede o servidor de subir
    _log.exception("Falha na importação/limpeza de planilhas antigas")
# Agente de Publicação: retoma os posts agendados (THYFIA_AGENDADOR=0 desliga)
for _nome in ("thyfia.publicacao", "thyfia.instagram", "thyfia.imagem"):
    logging.getLogger(_nome).setLevel(logging.INFO)
agente_publicacao.iniciar_agendador()
piloto.iniciar_agendador()  # Piloto Automático (THYFIA_PILOTO=0 desliga)
try:  # aproveita os preços dos itens já gerados como primeiro histórico (só na primeira vez)
    aprendizado.reconstruir_precos()
except Exception:
    logging.getLogger("thyfia").warning("Não consegui reconstruir o histórico de preços.", exc_info=True)

app = FastAPI(title="ThyfIA", docs_url=None, redoc_url=None)


@app.middleware("http")
async def sem_cache_do_front(request: Request, call_next):
    """A página e os arquivos do front sempre revalidam (304 quando iguais): depois de um deploy
    ninguém fica com HTML novo + JS/CSS antigo em cache (tela de login "travada")."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")
# fotos enviadas para posts (nomes aleatórios) — o Instagram precisa buscá-las por URL pública
app.mount("/midia", StaticFiles(directory=db.MIDIA), name="midia")


# --------------------------------------------------------------------------- modelos
class Cadastro(BaseModel):
    nome: str = Field(max_length=80)
    email: str = Field(max_length=120)
    senha: str = Field(max_length=128)


class Login(BaseModel):
    email: str = Field(max_length=120)
    senha: str = Field(max_length=128)


class EsqueciSenha(BaseModel):
    email: str = Field(max_length=120)


class RedefinirSenha(BaseModel):
    token: str = Field(max_length=200)
    senha: str = Field(max_length=128)


class CartaoDom(BaseModel):
    url: str = Field(default="", max_length=3000)
    titulo: str = Field(default="", max_length=400)
    texto: str = Field(default="", max_length=3000)
    imagem: str = Field(default="", max_length=1000)


class NovoJob(BaseModel):
    termo: str = Field(min_length=2, max_length=80)
    limite: int = Field(default=20, ge=1, le=50)
    trechos: str | None = Field(default=None, max_length=3_000_000)  # cartões lidos pela extensão
    cartoes_dom: list[CartaoDom] | None = Field(default=None, max_length=400)  # cartões renderizados lidos pela extensão ≥ 1.5.0
    na_extensao: bool = False  # os links são gerados no navegador da pessoa (extensão ≥ 1.4.0)
    nicho: str = Field(default="", max_length=20)  # para qual grupo vai (vazio = o Cérebro decide)
    fonte: str = Field(default="ml", pattern="^(ml|shopee)$")  # de qual loja vêm os produtos


class ConectarShopee(BaseModel):
    app_id: str = Field(max_length=30)
    secret: str = Field(max_length=250)


class LinkShopee(BaseModel):
    url: str = Field(max_length=1000)


class ResultadoLink(BaseModel):
    url: str = Field(max_length=1000)
    link: str | None = Field(default=None, max_length=300)


class AbortarJob(BaseModel):
    erro: str = Field(default="", max_length=300)


# --------------------------------------------------------------------------- helpers
def usuario_atual(request: Request):
    u = auth.usuario_da_sessao(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Faça login para continuar.")
    return u


def exige(recurso: str):
    """Dependência: só passa quem tem o plano que libera o recurso (senão 402 → tela de planos)."""
    def dep(u=Depends(usuario_atual)):
        if not planos.tem_recurso(u, recurso):
            minimo = planos.PLANOS[planos.RECURSOS[recurso]]["nome"]
            raise HTTPException(402, f"{planos.NOMES_RECURSO[recurso].capitalize()} faz parte do plano {minimo} ou superior. "
                                     "Conheça os planos para liberar.")
        return u
    return dep


def exige_automacao_pronta(u=Depends(exige("automacao"))):
    if not tenant.automacao_pronta(u["id"]):
        raise HTTPException(409, "Antes de enviar, informe seu WhatsApp em Automação (menu lateral).")
    return u


def admin_atual(u=Depends(usuario_atual)):
    if not planos.e_admin(u):
        raise HTTPException(403, "Área restrita ao administrador.")
    return u


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600,
                    secure=os.environ.get("THYFIA_COOKIE_SECURE") == "1")  # 1 = atrás de HTTPS (produção)


def _job_dict(r) -> dict:
    d = {k: r[k] for k in ("id", "termo", "limite", "status", "total", "feitos", "ok", "erro",
                           "criado", "finalizado", "envio_status", "envio_em", "envio_erro")}
    d["fonte"] = r["fonte"] if "fonte" in r.keys() else "ml"
    total, com_link = itens.contar(r["id"])
    d["itens"], d["links"] = total, com_link
    # a planilha é gerada do banco; o arquivo local é só uma cópia opcional
    d["planilha"] = r["status"] == "concluido" and (total > 0 or bool(r["arquivo"] and Path(r["arquivo"]).exists()))
    try:
        d["envio_resumo"] = json.loads(r["envio_resumo"]) if r["envio_resumo"] else None
    except ValueError:
        d["envio_resumo"] = None
    try:
        d["captura"] = json.loads(r["captura"]) if "captura" in r.keys() and r["captura"] else None
    except ValueError:
        d["captura"] = None
    return d


def _job_ativo(uid: int):
    r = db.conn().execute(
        f"SELECT * FROM jobs WHERE usuario_id=? AND status IN ({','.join('?' * len(ATIVOS))}) "
        "ORDER BY criado DESC LIMIT 1", (uid, *ATIVOS)).fetchone()
    return _job_dict(r) if r else None


def _preferencias(uid: int) -> dict:
    r = db.conn().execute("SELECT auto_enviar FROM usuarios WHERE id=?", (uid,)).fetchone()
    return {"auto_enviar": bool(r and r["auto_enviar"]),
            # a plataforma tem n8n habilitado E o cliente já informou onde divulgar
            "automacao_configurada": automacao.configurada() and tenant.automacao_pronta(uid)}


def _perfil_usuario(u) -> dict:
    sit = planos.situacao(u)
    return {"id": u["id"], "nome": u["nome"], "email": u["email"], "admin": sit["admin"],
            "plano": sit, "liberados": planos.liberados(u),
            "ml": motor_ml.status_ml(u["id"]), "shopee": shopee.status(u["id"]), "job_ativo": _job_ativo(u["id"]),
            "preferencias": _preferencias(u["id"])}


# --------------------------------------------------------------------------- páginas
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# --------------------------------------------------------------------------- conta
@app.post("/api/cadastro")
def cadastro(dados: Cadastro, resp: Response):
    try:
        uid = auth.criar_usuario(dados.nome, dados.email, dados.senha)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.garantir_admin()
    emails.enviar_em_segundo_plano(dados.nome, dados.email.strip().lower())  # boas-vindas; sem config, não faz nada
    _set_cookie(resp, auth.criar_sessao(uid))
    return _perfil_usuario(auth.autenticar(dados.email, dados.senha))


@app.post("/api/login")
def login(dados: Login, resp: Response):
    u = auth.autenticar(dados.email, dados.senha)
    if not u:
        raise HTTPException(401, "E-mail ou senha incorretos.")
    _set_cookie(resp, auth.criar_sessao(u["id"]))
    return _perfil_usuario(u)


@app.post("/api/senha/esqueci")
def senha_esqueci(dados: EsqueciSenha):
    """Pede o link de redefinição. A resposta é sempre a mesma, exista a conta ou não (não revela quem tem conta)."""
    pedido = auth.pedir_redefinicao(dados.email)
    if pedido:
        nome, email, token = pedido
        emails.enviar_redefinicao_em_segundo_plano(nome, email, token)
    return {"ok": True}


@app.post("/api/senha/redefinir")
def senha_redefinir(dados: RedefinirSenha):
    try:
        email = auth.redefinir_senha(dados.token, dados.senha)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "email": email}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    auth.encerrar_sessao(request.cookies.get(COOKIE))
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/eu")
def eu(u=Depends(usuario_atual)):
    return _perfil_usuario(u)


# --------------------------------------------------------------------------- mercado livre
@app.get("/api/ml/status")
def ml_status(u=Depends(usuario_atual)):
    return motor_ml.status_ml(u["id"])


@app.post("/api/ml/conectar")
def ml_conectar(u=Depends(exige("gerar"))):
    if motor_ml.status_ml(u["id"])["status"] == "conectando":
        return motor_ml.status_ml(u["id"])
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Aguarde a geração de links terminar.")
    motor_ml._set_status_ml(u["id"], "conectando", "Abrindo o Mercado Livre…")
    threading.Thread(target=motor_ml.conectar, args=(u["id"],), daemon=True).start()
    return motor_ml.status_ml(u["id"])


class Sessao(BaseModel):
    cookies: list[dict] = Field(max_length=400)


@app.post("/api/ml/sessao")
def ml_sessao(dados: Sessao, u=Depends(exige("gerar"))):
    """Recebe a sessão do Mercado Livre do navegador da pessoa (extensão ThyfIA Conector)."""
    try:
        motor_ml.salvar_sessao(u["id"], dados.cookies)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return motor_ml.status_ml(u["id"])


EXTENSAO_DIR = Path(__file__).resolve().parent.parent / "extensao_thyfia"
AJUDA_HTML = Path(__file__).resolve().parent / "conteudo" / "ajuda.html"


@app.get("/api/extensao")
def info_extensao():
    return {"pasta": str(EXTENSAO_DIR)}


@app.get("/api/extensao/download")
def baixar_extensao(u=Depends(usuario_atual)):
    """Extensão ThyfIA Conector em .zip (para quem acessa pelo domínio e não tem a pasta local)."""
    if not EXTENSAO_DIR.is_dir():
        raise HTTPException(404, "Extensão indisponível neste servidor.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arq in sorted(EXTENSAO_DIR.rglob("*")):
            if arq.is_file():
                z.write(arq, Path("extensao_thyfia") / arq.relative_to(EXTENSAO_DIR))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="extensao_thyfia.zip"'})


@app.get("/api/ajuda")
def ajuda(u=Depends(usuario_atual)):
    """Guia de uso — só para quem está logado (por isso não fica na pasta /static pública)."""
    return Response(AJUDA_HTML.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/ml/desconectar")
def ml_desconectar(u=Depends(usuario_atual)):
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Aguarde a geração de links terminar.")
    motor_ml.desconectar(u["id"])
    return motor_ml.status_ml(u["id"])


# --------------------------------------------------------------------------- shopee
@app.get("/api/shopee/status")
def shopee_status(u=Depends(usuario_atual)):
    return shopee.status(u["id"])


@app.post("/api/shopee/conectar")
def shopee_conectar(dados: ConectarShopee, u=Depends(exige("gerar"))):
    """Guarda o App ID e o Secret da Open API da Shopee (depois de testá-los de verdade). O Secret fica cifrado."""
    try:
        return shopee.conectar(u["id"], dados.app_id, dados.secret)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/shopee/desconectar")
def shopee_desconectar(u=Depends(usuario_atual)):
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Aguarde a geração de links terminar.")
    return shopee.desconectar(u["id"])


@app.post("/api/shopee/link")
def shopee_link(dados: LinkShopee, u=Depends(exige("gerar"))):
    """Link de afiliado de um produto ou loja da Shopee que a pessoa colou ("Link personalizado")."""
    if shopee.status(u["id"])["status"] != "conectado":
        raise HTTPException(400, "Conecte sua conta da Shopee primeiro, em Integrações.")
    try:
        return {"link": shopee.gerar_link(u["id"], dados.url)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- jobs
@app.post("/api/jobs")
def criar_job(dados: NovoJob, u=Depends(exige("gerar"))):
    if dados.fonte == "shopee":
        if shopee.status(u["id"])["status"] != "conectado":
            raise HTTPException(400, "Conecte sua conta da Shopee primeiro, em Integrações.")
    elif motor_ml.status_ml(u["id"])["status"] != "conectado":
        raise HTTPException(400, "Conecte sua conta do Mercado Livre primeiro.")
    # geração feita pelo navegador que a pessoa fechou no meio nunca termina: libera depois de um tempo
    c = db.conn()
    c.execute(f"UPDATE jobs SET status='erro', erro='A geração foi interrompida (a página foi fechada). Tente de novo.' "
              f"WHERE usuario_id=? AND status IN ({','.join('?' * len(ATIVOS))}) "
              "AND criado < datetime('now','-20 minutes','localtime')", (u["id"], *ATIVOS))
    c.commit()
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Você já tem uma geração em andamento.")
    job_id = secrets.token_hex(8)
    termo = dados.termo.strip()
    if dados.nicho and dados.nicho != nichos.GERAL and planos.tem_recurso(u, "automacao"):
        try:  # a escolha vale para esta busca (e ensina o Cérebro); sem plano Pro não há grupos, então é ignorada
            aprendizado.corrigir_nicho(u["id"], termo, dados.nicho)
        except ValueError as e:
            raise HTTPException(400, str(e))
    c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, fonte) VALUES (?,?,?,?,?)",
              (job_id, u["id"], termo, dados.limite, dados.fonte))
    c.commit()
    if dados.fonte == "shopee":  # direto na API da Shopee: sem navegador nem extensão
        threading.Thread(target=shopee.executar_job, args=(job_id, u["id"], termo, dados.limite), daemon=True).start()
        return {"id": job_id, "modo": "shopee"}
    if dados.na_extensao and (dados.trechos or dados.cartoes_dom):
        # os links saem do navegador da pessoa: o servidor só prepara a lista (sem abrir o Chrome)
        motor_ml.preparar_job_extensao(job_id, u["id"], termo, dados.limite, dados.trechos,
                                       [c.model_dump() for c in dados.cartoes_dom or []])
        return {"id": job_id, "modo": "extensao"}
    threading.Thread(target=motor_ml.executar_job, args=(job_id, u["id"], termo, dados.limite),
                     kwargs={"trechos": dados.trechos}, daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs")
def listar_jobs(u=Depends(usuario_atual)):
    rows = db.conn().execute(
        "SELECT * FROM jobs WHERE usuario_id=? ORDER BY criado DESC LIMIT 50", (u["id"],)).fetchall()
    return [_job_dict(r) for r in rows]


def _job_do_usuario(job_id: str, uid: int):
    r = db.conn().execute("SELECT * FROM jobs WHERE id=? AND usuario_id=?", (job_id, uid)).fetchone()
    if not r:
        raise HTTPException(404, "Geração não encontrada.")
    return r


@app.get("/api/jobs/{job_id}")
def ver_job(job_id: str, u=Depends(usuario_atual)):
    return _job_dict(_job_do_usuario(job_id, u["id"]))


@app.get("/api/jobs/{job_id}/pendentes")
def job_pendentes(job_id: str, u=Depends(usuario_atual)):
    """Produtos que ainda aguardam o link (a extensão gera no navegador da pessoa)."""
    _job_do_usuario(job_id, u["id"])
    return {"urls": itens.pendentes(job_id)}


@app.post("/api/jobs/{job_id}/links")
def job_receber_link(job_id: str, dados: ResultadoLink, u=Depends(usuario_atual)):
    _job_do_usuario(job_id, u["id"])
    motor_ml.registrar_link(job_id, u["id"], dados.url, dados.link)
    return _job_dict(_job_do_usuario(job_id, u["id"]))


@app.post("/api/jobs/{job_id}/abortar")
def job_abortar(job_id: str, dados: AbortarJob, u=Depends(usuario_atual)):
    _job_do_usuario(job_id, u["id"])
    motor_ml.abortar_job(job_id, u["id"], dados.erro)
    return _job_dict(_job_do_usuario(job_id, u["id"]))


@app.get("/api/jobs/{job_id}/planilha")
def baixar_planilha(job_id: str, u=Depends(usuario_atual)):
    """Planilha gerada na hora a partir do banco (mesmo formato de sempre). Se a geração ainda
    não tem itens no banco, serve a cópia local antiga, se existir."""
    r = _job_do_usuario(job_id, u["id"])
    slug = "".join(ch if ch.isalnum() else "-" for ch in r["termo"].lower()).strip("-")
    nome = f"thyfia-links-{slug}.xlsx"
    tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if itens.contar(job_id)[0]:
        return Response(itens.gerar_planilha(job_id, r["termo"]), media_type=tipo,
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    if r["arquivo"] and Path(r["arquivo"]).exists():
        return FileResponse(r["arquivo"], filename=nome, media_type=tipo)
    raise HTTPException(404, "Planilha indisponível: essa geração não tem itens salvos.")


# --------------------------------------------------------------------------- automação (n8n)
@app.post("/api/jobs/{job_id}/enviar")
def enviar_job(job_id: str, u=Depends(exige_automacao_pronta)):
    """Envia ao webhook do n8n os links da geração, lidos do BANCO (não depende da planilha
    local). A chamada sai daqui (o token nunca vai ao navegador). Reenviar é seguro: o n8n
    ignora duplicados."""
    r = _job_do_usuario(job_id, u["id"])
    if r["status"] != "concluido":
        raise HTTPException(400, "Essa geração ainda não terminou.")
    if r["envio_status"] == "enviando":
        raise HTTPException(409, "Esse envio já está em andamento.")
    try:
        motor_ml.enviar_para_automacao(job_id, r["termo"])
    except automacao.ErroAutomacao as e:
        status = {"config": 503, "token": 502, "vazio": 400}.get(e.codigo, 502)
        raise HTTPException(status, str(e))
    return _job_dict(_job_do_usuario(job_id, u["id"]))


class Preferencias(BaseModel):
    auto_enviar: bool


@app.post("/api/preferencias")
def salvar_preferencias(dados: Preferencias, u=Depends(usuario_atual)):
    if dados.auto_enviar:
        if not planos.tem_recurso(u, "automacao"):
            raise HTTPException(402, "O envio automático faz parte do plano Pro ou superior.")
        if not tenant.automacao_pronta(u["id"]):
            raise HTTPException(409, "Informe seu WhatsApp em Automação antes de ligar o envio automático.")
    c = db.conn()
    c.execute("UPDATE usuarios SET auto_enviar=? WHERE id=?", (int(dados.auto_enviar), u["id"]))
    c.commit()
    return _preferencias(u["id"])


# --------------------------------------------------------------------------- Agente de Publicação
_cache_ig: dict = {"em": 0.0, "dados": None, "uid": None}  # cache de 60 s, por cliente


def _erro_pub(e: Exception):
    if isinstance(e, agente_publicacao.ErroPublicacao):
        return HTTPException(e.status, str(e))
    if isinstance(e, (imagem_host.ErroImagem, video.ErroVideo)):
        return HTTPException(400, str(e))
    if isinstance(e, facebook.ErroFacebook):
        return HTTPException(502, str(e))
    if isinstance(e, instagram.ErroInstagram):
        return HTTPException(503 if e.codigo == "config" else 502, str(e))
    raise e


@app.get("/api/instagram/status")
def instagram_status(atualizar: bool = False, u=Depends(exige("agente"))):
    """Configurado? @username? limite restante? (o token nunca sai do backend)"""
    base = {"agendador_ativo": agente_publicacao.agendador_ativo(), "aviso": agente_publicacao.AVISO_AGENDADOR,
            "url_publica": bool(imagem_host.url_base_publica())}
    with tenant.ig_ctx(u["id"]):
        if not instagram.configurado():
            return {**base, "configurado": False, "mensagem": instagram.NAO_CONFIGURADO}
        if (not atualizar and _cache_ig["dados"] and _cache_ig.get("uid") in (None, u["id"])
                and time.time() - _cache_ig["em"] < 60):
            return {**base, **_cache_ig["dados"]}
        dados = {"configurado": True, "username": None, "host": None, "limite": None, "erro": None}
        try:
            conta = instagram.validar_conta()
            dados.update(username=conta["username"], host=conta["host"])
            dados["limite"] = instagram.limite_publicacao()
        except instagram.ErroInstagram as e:
            dados["erro"] = str(e)
    _cache_ig.update(em=time.time(), dados=dados, uid=u["id"])
    return {**base, **dados}


@app.post("/api/instagram/validar")
def instagram_validar(u=Depends(exige("agente"))):
    """Rota de teste: só chama validar_conta() (não publica nada)."""
    try:
        with tenant.ig_ctx(u["id"]):
            return instagram.validar_conta()
    except instagram.ErroInstagram as e:
        raise _erro_pub(e)


class Sugerir(BaseModel):
    keyword: str | None = Field(default=None, max_length=80)
    job_id: str | None = Field(default=None, max_length=32)
    quantidade: int = Field(default=5, ge=1, le=20)
    canal: str = Field(default="instagram", max_length=20)


class FotoCarrossel(BaseModel):
    url: str = Field(max_length=2000)
    item_id: int | None = None


class NovaPublicacao(BaseModel):
    item_id: int | None = None          # None = post manual (foto enviada)
    legenda: str = Field(default="", max_length=2200)  # story não tem legenda
    image_url: str = Field(default="", max_length=2000)  # carrossel: a capa vem de `midias`
    agendado_para: str | None = Field(default=None, max_length=40)
    confirmar: bool = False
    canal: str = Field(default="instagram", max_length=20)
    formato: str = Field(default="feed", max_length=20)
    midias: list[FotoCarrossel] | None = Field(default=None, max_length=10)  # carrossel: 2 a 10 fotos
    video_url: str | None = Field(default=None, max_length=300)  # reels: /midia/<nome>.mp4 (já enviado/gerado)
    link: str | None = Field(default=None, max_length=500)  # facebook "publicação com link"

    def fotos(self) -> list[dict] | None:
        return [m.model_dump() for m in self.midias] if self.midias is not None else None


@app.post("/api/publicacao/sugerir")
def publicacao_sugerir(dados: Sugerir, u=Depends(exige("agente"))):
    canal = dados.canal if dados.canal in agente_publicacao.CANAIS else "instagram"
    return agente_publicacao.sugerir(u["id"], dados.keyword, dados.job_id, dados.quantidade, canal=canal)


@app.get("/api/publicacao/canais")
def publicacao_canais(u=Depends(exige("agente"))):
    """Estado de cada canal (conectado ou não) e os formatos que a tela oferece."""
    t = tenant.publica(u["id"])
    return {
        "formatos": {c: list(f) for c, f in agente_publicacao.CANAIS.items()},
        "publicaveis": sorted(f"{c}/{f}" for c, f in agente_publicacao.PUBLICAVEIS),
        "instagram": {"configurado": t["instagram"]["configurado"], "username": t["instagram"]["username"]},
        "facebook": {"configurado": t["facebook"]["configurado"], "nome": t["facebook"]["nome"]},
        "whatsapp": {"numero": t["whatsapp_formatado"], "telegram": t["telegram_canal"],
                     "automacao_pronta": tenant.automacao_pronta(u["id"])},
    }


@app.get("/api/publicacao/palavras")
def publicacao_palavras(canal: str = "instagram", u=Depends(exige("agente"))):
    return {"palavras": agente_publicacao.palavras_chave(u["id"], canal if canal in agente_publicacao.CANAIS else "instagram")}


@app.post("/api/publicacao/imagem")
async def publicacao_imagem(request: Request, u=Depends(exige("agente"))):
    """Foto enviada pela pessoa (corpo = bytes da imagem). Vira JPEG em dados/midia."""
    corpo = await request.body()
    try:
        return imagem_host.salvar_upload(corpo, db.MIDIA)
    except imagem_host.ErroImagem as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/video")
async def publicacao_video(request: Request, u=Depends(exige("agente"))):
    """Vídeo enviado pela pessoa (corpo = bytes do arquivo) → MP4 9:16 pronto para Reels."""
    corpo = await request.body()
    try:
        return await run_in_threadpool(video.salvar_upload, corpo, db.MIDIA)
    except video.ErroVideo as e:
        raise _erro_pub(e)


class GerarReels(BaseModel):
    midias: list[FotoCarrossel] = Field(max_length=10)
    com_preco: bool = True


@app.post("/api/publicacao/reels/gerar")
def publicacao_reels_gerar(dados: GerarReels, u=Depends(exige("agente"))):
    """Monta um vídeo (3 s por foto, com título e preço) a partir das fotos escolhidas."""
    fotos = []
    try:
        for m in dados.midias:
            titulo = preco = None
            if m.item_id is not None and dados.com_preco:
                it = db.conn().execute("SELECT i.titulo, i.preco FROM job_itens i JOIN jobs j ON j.id=i.job_id "
                                       "WHERE i.id=? AND j.usuario_id=?", (m.item_id, u["id"])).fetchone()
                if not it:
                    raise HTTPException(404, "Item não encontrado.")
                titulo, preco = it["titulo"], agente_publicacao.preco_br(it["preco"]) if it["preco"] else None
            fotos.append({"bytes": imagem_host._bytes_da_imagem(m.url, db.MIDIA), "titulo": titulo, "preco": preco})
        return video.gerar_de_fotos(fotos, db.MIDIA)
    except Exception as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/agendar")
def publicacao_agendar(dados: NovaPublicacao, u=Depends(exige("agente"))):
    if not dados.agendado_para:
        raise HTTPException(400, "Escolha a data e a hora da publicação.")
    try:
        return agente_publicacao.agendar(u["id"], dados.item_id, dados.legenda, dados.image_url, dados.agendado_para,
                                         dados.canal, dados.formato, dados.fotos(), dados.video_url, dados.link)
    except Exception as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/publicar-agora")
def publicacao_publicar_agora(dados: NovaPublicacao, u=Depends(exige("agente"))):
    try:
        with tenant.ig_ctx(u["id"]):
            pub = agente_publicacao.publicar_agora(u["id"], dados.item_id, dados.legenda, dados.image_url,
                                                   dados.confirmar, dados.canal, dados.formato, dados.fotos(),
                                                   dados.video_url, dados.link)
    except Exception as e:
        raise _erro_pub(e)
    _cache_ig["em"] = 0  # o limite mudou
    return pub


# --------------------------------------------------------------------------- Piloto Automático (Pro)
class PilotoConfig(BaseModel):
    ativo: bool | None = None
    modo: str | None = None
    hora_ini: int | None = None
    hora_fim: int | None = None
    intervalo_min: int | None = None
    max_dia: int | None = None
    validade_dias: int | None = None
    preco_min: float | None = None
    preco_max: float | None = None
    palavras: list[str] | None = None
    estilo: str | None = None
    relatorio: bool | None = None
    rastrear: bool | None = None


class PilotoNichoBusca(BaseModel):
    busca: str = Field(max_length=120)
    nicho: str = Field(default="", max_length=20)  # vazio = voltar ao automático


class PilotoDestino(BaseModel):
    tipo: str = Field(max_length=20)
    entrada: str = Field(max_length=300)
    nome: str = Field(default="", max_length=80)
    meu_numero: bool = False
    nicho: str = Field(default="geral", max_length=20)


class PilotoGrupos(BaseModel):
    jids: list[str] = Field(max_length=60)
    nichos: dict[str, str] = Field(default_factory=dict, max_length=60)  # jid → nicho


class PilotoCodigo(BaseModel):
    codigo: str = Field(max_length=10)


class PilotoAtivo(BaseModel):
    ativo: bool | None = None
    nicho: str | None = Field(default=None, max_length=20)


def _erro_piloto(e: Exception) -> HTTPException:
    if isinstance(e, piloto.ErroPiloto):
        return HTTPException(e.status, str(e))
    logging.getLogger("thyfia.piloto").exception("erro inesperado no Piloto")
    return HTTPException(500, "Algo deu errado no Piloto Automático. Tente de novo.")


@app.get("/api/piloto")
def piloto_status(u=Depends(exige("automacao"))):
    return piloto.status(u["id"])


@app.put("/api/piloto/config")
def piloto_config(dados: PilotoConfig, u=Depends(exige("automacao"))):
    try:
        piloto.salvar_config(u["id"], dados.model_dump(exclude_unset=True))
    except Exception as e:
        raise _erro_piloto(e)
    return piloto.status(u["id"])


@app.get("/api/piloto/whatsapp")
def piloto_whatsapp(u=Depends(exige("automacao"))):
    return piloto.whatsapp_status(u["id"], atualizar=True)


@app.post("/api/piloto/whatsapp/conectar")
def piloto_whatsapp_conectar(u=Depends(exige("automacao"))):
    try:
        return piloto.whatsapp_conectar(u["id"])
    except Exception as e:
        raise _erro_piloto(e)


@app.post("/api/piloto/whatsapp/desconectar")
def piloto_whatsapp_desconectar(u=Depends(exige("automacao"))):
    try:
        return piloto.whatsapp_desconectar(u["id"])
    except Exception as e:
        raise _erro_piloto(e)


@app.get("/api/piloto/whatsapp/grupos")
def piloto_whatsapp_grupos(u=Depends(exige("automacao"))):
    try:
        return piloto.grupos_whatsapp(u["id"])
    except Exception as e:
        raise _erro_piloto(e)


@app.put("/api/piloto/whatsapp/grupos")
def piloto_whatsapp_grupos_salvar(dados: PilotoGrupos, u=Depends(exige("automacao"))):
    try:
        return piloto.salvar_grupos(u["id"], dados.jids, dados.nichos)
    except Exception as e:
        raise _erro_piloto(e)


@app.post("/api/piloto/destinos")
def piloto_destino_novo(dados: PilotoDestino, u=Depends(exige("automacao"))):
    try:
        return piloto.adicionar_destino(u["id"], dados.tipo, dados.entrada, dados.nome, dados.meu_numero, dados.nicho)
    except Exception as e:
        raise _erro_piloto(e)


@app.post("/api/piloto/destinos/{did}/confirmar")
def piloto_destino_confirmar(did: int, dados: PilotoCodigo, u=Depends(exige("automacao"))):
    try:
        return piloto.confirmar_destino(u["id"], did, dados.codigo)
    except Exception as e:
        raise _erro_piloto(e)


@app.get("/api/nicho-sugerido")
def nicho_sugerido(busca: str = Query(default="", max_length=120), u=Depends(usuario_atual)):
    """Nicho que o Cérebro deduz para o que a pessoa está digitando (a tela de Gerar links mostra e deixa trocar)."""
    n, origem = aprendizado.nicho_de(u["id"], busca) if busca.strip() else (None, "")
    return {"nicho": n, "origem": origem, "nome": nichos.nome(n) if n else "", "emoji": nichos.emoji(n) if n else "",
            "catalogo": [x for x in nichos.catalogo() if x["slug"] != nichos.GERAL]}


@app.put("/api/piloto/nicho-busca")
def piloto_nicho_busca(dados: PilotoNichoBusca, u=Depends(exige("automacao"))):
    """O cliente corrige o nicho de uma busca; vale para ele e ensina o Cérebro."""
    try:
        aprendizado.corrigir_nicho(u["id"], dados.busca, dados.nicho)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return piloto.status(u["id"])


@app.put("/api/piloto/destinos/{did}")
def piloto_destino_alternar(did: int, dados: PilotoAtivo, u=Depends(exige("automacao"))):
    try:
        if dados.ativo is None and dados.nicho is None:
            raise piloto.ErroPiloto("Nada para alterar.")
        d = None
        if dados.ativo is not None:
            d = piloto.alternar_destino(u["id"], did, dados.ativo)
        if dados.nicho is not None:
            d = piloto.definir_nicho(u["id"], did, dados.nicho)
        return d
    except Exception as e:
        raise _erro_piloto(e)


@app.delete("/api/piloto/destinos/{did}")
def piloto_destino_remover(did: int, u=Depends(exige("automacao"))):
    try:
        piloto.remover_destino(u["id"], did)
    except Exception as e:
        raise _erro_piloto(e)
    return {"ok": True}


@app.post("/api/piloto/enviar-agora")
def piloto_enviar_agora(u=Depends(exige("automacao"))):
    try:
        r = piloto.planejar(u["id"], forcar=True)
        if r.get("acao") == "planejou":
            piloto.despachar(u["id"])
        return r
    except Exception as e:
        raise _erro_piloto(e)


@app.post("/api/piloto/aprovar/{item_id}")
def piloto_aprovar(item_id: int, u=Depends(exige("automacao"))):
    try:
        return {"destinos": piloto.aprovar(u["id"], item_id)}
    except Exception as e:
        raise _erro_piloto(e)


@app.post("/api/piloto/pular/{item_id}")
def piloto_pular(item_id: int, u=Depends(exige("automacao"))):
    try:
        return {"destinos": piloto.pular(u["id"], item_id)}
    except Exception as e:
        raise _erro_piloto(e)


@app.get("/api/publicacao")
def publicacao_listar(u=Depends(exige("agente"))):
    return agente_publicacao.listar(u["id"])


@app.put("/api/publicacao/{pub_id}")
def publicacao_editar(pub_id: int, dados: NovaPublicacao, u=Depends(exige("agente"))):
    """Altera uma publicação agendada (legenda, foto, data/hora)."""
    if not dados.agendado_para:
        raise HTTPException(400, "Escolha a data e a hora da publicação.")
    try:
        return agente_publicacao.editar(u["id"], pub_id, dados.legenda, dados.image_url, dados.agendado_para,
                                        dados.fotos(), dados.link)
    except Exception as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/{pub_id}/cancelar")
def publicacao_cancelar(pub_id: int, u=Depends(exige("agente"))):
    try:
        return agente_publicacao.cancelar(u["id"], pub_id)
    except Exception as e:
        raise _erro_pub(e)


# --------------------------------------------------------------------------- cérebro
@app.get("/api/cerebro/sugestoes")
def sugestoes(q: str = "", u=Depends(exige("gerar"))):
    return {"sugestoes": cerebro.sugerir(q[:80]), "relacionados": cerebro.relacionados(q[:80]) if q else []}


@app.get("/api/cerebro/aprendizado")
def cerebro_aprendizado(u=Depends(usuario_atual)):
    """O que o Cérebro aprendeu: palavras novas por nicho, quedas de preço e o que o público DESTE usuário responde."""
    return aprendizado.resumo(u["id"])


@app.get("/r/{token}")
def clique_rastreado(token: str, request: Request):
    """Link curto das ofertas do Piloto (quando o cliente liga a medição de cliques): conta o clique de gente de
    verdade e leva ao link de afiliado. Atrás de proxy, o IP vem do cabeçalho."""
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    destino = aprendizado.registrar_clique(token[:32], ip, request.headers.get("user-agent", ""))
    if not destino:
        raise HTTPException(404, "Link não encontrado.")
    return RedirectResponse(destino, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/api/cerebro/stats")
def stats(u=Depends(usuario_atual)):
    return cerebro.estatisticas()


# --------------------------------------------------------------------------- planos e assinatura
def _url_publica(request: Request) -> str:
    base = configuracoes.obter("url_publica") or str(request.base_url)
    return base.rstrip("/")


def _link_checkout(plano: str, email: str) -> str:
    link = configuracoes.obter(f"kiwify_link_{plano}")
    if not link:
        return ""
    from urllib.parse import quote
    return link + ("&" if "?" in link else "?") + "email=" + quote(email)  # a Kiwify pré-preenche o e-mail


@app.get("/api/planos")
def listar_planos(u=Depends(usuario_atual)):
    """Catálogo para a tela de planos: preços, o que cada um inclui, link de compra e o plano atual."""
    sit = planos.situacao(u)
    cat = []
    for p in planos.catalogo():
        cat.append({**p, "checkout": _link_checkout(p["id"], u["email"]),
                    "atual": sit["plano"] == p["id"], "inclui_atual": planos.nivel(sit["plano"]) >= p["nivel"]})
    return {"planos": cat, "situacao": sit, "suporte_whatsapp": configuracoes.obter("suporte_whatsapp"),
            "pagamento_configurado": any(p["checkout"] for p in cat)}


@app.get("/api/assinatura")
def minha_assinatura(u=Depends(usuario_atual)):
    return {"situacao": planos.situacao(u), "liberados": planos.liberados(u)}


@app.post("/api/kiwify/webhook")
async def kiwify_webhook(request: Request):
    """Chamado pela Kiwify a cada evento de venda/assinatura. Pública, mas só aceita chamadas assinadas."""
    token = configuracoes.obter("kiwify_token")
    if not token:
        raise HTTPException(503, "Webhook não configurado.")
    corpo = await request.body()
    ok = (kiwify.assinatura_confere(corpo, request.query_params.get("signature"), token)
          or kiwify.token_na_url_confere(request.query_params.get("token"), token))
    if not ok:
        kiwify.registrar("?", "", "", None, "recusado", "Assinatura inválida.")
        raise HTTPException(401, "Assinatura inválida.")
    try:
        payload = json.loads(corpo.decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "JSON inválido.")
    return kiwify.processar(payload)


# --------------------------------------------------------------------------- configuração do cliente
class Canais(BaseModel):
    whatsapp: str = Field(max_length=40)
    telegram: str = Field(default="", max_length=80)


def _config_http(fn, *a):
    try:
        return fn(*a)
    except tenant.ErroConfig as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/conta/config")
def conta_config(u=Depends(usuario_atual)):
    return {**tenant.publica(u["id"]), "plataforma_ok": automacao.configurada()}


@app.put("/api/conta/canais")
def conta_canais(dados: Canais, u=Depends(exige("automacao"))):
    return _config_http(tenant.salvar_canais, u["id"], dados.whatsapp, dados.telegram)


@app.post("/api/conta/canais/testar-whatsapp")
def conta_testar_whatsapp(u=Depends(exige("automacao"))):
    return _config_http(tenant.testar_whatsapp, u["id"])


@app.post("/api/conta/canais/testar-telegram")
def conta_testar_telegram(u=Depends(exige("automacao"))):
    return _config_http(tenant.testar_telegram, u["id"])


class ContaInstagram(BaseModel):
    user_id: str = Field(max_length=40)
    token: str = Field(max_length=1000)


@app.put("/api/conta/instagram")
def conta_instagram_salvar(dados: ContaInstagram, u=Depends(exige("agente"))):
    r = _config_http(tenant.instagram_salvar, u["id"], dados.user_id, dados.token)
    _cache_ig["em"] = 0
    return r


@app.delete("/api/conta/instagram")
def conta_instagram_remover(u=Depends(exige("agente"))):
    r = tenant.instagram_remover(u["id"])
    _cache_ig["em"] = 0
    return r


class ContaFacebook(BaseModel):
    page_id: str = Field(max_length=40)
    token: str = Field(max_length=1000)


@app.put("/api/conta/facebook")
def conta_facebook_salvar(dados: ContaFacebook, u=Depends(exige("agente"))):
    return _config_http(tenant.facebook_salvar, u["id"], dados.page_id, dados.token)


@app.delete("/api/conta/facebook")
def conta_facebook_remover(u=Depends(exige("agente"))):
    return tenant.facebook_remover(u["id"])


@app.get("/api/onboarding")
def onboarding(u=Depends(usuario_atual)):
    return {"passos": tenant.checklist(u), "plano": planos.situacao(u)}


# --------------------------------------------------------------------------- admin (dono da plataforma)
@app.get("/api/admin/resumo")
def admin_resumo(u=Depends(admin_atual)):
    c = db.conn()
    total = c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    agora = planos.agora_iso()
    por_plano = {p: 0 for p in planos.ORDEM}
    receita = 0.0
    for r in c.execute("SELECT plano, COUNT(*) n FROM assinaturas WHERE status IN ('ativa','atrasada') "
                       "AND valido_ate > ? GROUP BY plano", (agora,)):
        por_plano[r["plano"]] = r["n"]
        receita += r["n"] * planos.PLANOS[r["plano"]]["preco"]
    return {"usuarios": total, "assinantes": por_plano, "receita_mensal": round(receita, 2),
            "jobs_30d": c.execute("SELECT COUNT(*) FROM jobs WHERE criado >= datetime('now','-30 day','localtime')").fetchone()[0],
            "webhook_url": "/api/kiwify/webhook"}


@app.get("/api/admin/usuarios")
def admin_usuarios(busca: str = "", u=Depends(admin_atual)):
    q = f"%{busca.strip().lower()}%"
    rows = db.conn().execute(
        "SELECT * FROM usuarios WHERE lower(email) LIKE ? OR lower(nome) LIKE ? ORDER BY id DESC LIMIT 200", (q, q)).fetchall()
    out = []
    for r in rows:
        sit = planos.situacao(r)
        out.append({"id": r["id"], "nome": r["nome"], "email": r["email"], "papel": r["papel"], "ativo": bool(r["ativo"]),
                    "criado": r["criado"], "plano": sit["plano"], "status": sit["status"], "valido_ate": sit["valido_ate"],
                    "origem": sit["origem"], "ml": motor_ml.status_ml(r["id"])["status"],
                    "jobs": db.conn().execute("SELECT COUNT(*) FROM jobs WHERE usuario_id=?", (r["id"],)).fetchone()[0],
                    "assinaturas": sit["assinaturas"]})
    return out


class Concessao(BaseModel):
    email: str = Field(max_length=120)
    plano: str = Field(max_length=10)
    dias: int = Field(default=31, ge=1, le=3650)
    observacao: str = Field(default="", max_length=200)


@app.post("/api/admin/assinaturas")
def admin_conceder(dados: Concessao, u=Depends(admin_atual)):
    """Libera (ou renova) um plano manualmente — cortesia, teste ou correção de um pagamento."""
    if not auth.RE_EMAIL.match(dados.email.strip()):
        raise HTTPException(400, "E-mail inválido.")
    try:
        planos.conceder(dados.email, dados.plano, dados.dias, origem="manual", observacao=dados.observacao or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/admin/assinaturas")
def admin_revogar(email: str, plano: str, u=Depends(admin_atual)):
    planos.marcar(email, plano, "reembolsada")  # corta o acesso na hora (vale para manual e Kiwify)
    return {"ok": True}


class EstadoConta(BaseModel):
    ativo: bool | None = None
    admin: bool | None = None


@app.put("/api/admin/usuarios/{uid}")
def admin_editar_usuario(uid: int, dados: EstadoConta, u=Depends(admin_atual)):
    alvo = db.conn().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not alvo:
        raise HTTPException(404, "Usuário não encontrado.")
    if uid == u["id"] and (dados.ativo is False or dados.admin is False):
        raise HTTPException(400, "Você não pode desativar nem rebaixar a si mesmo.")
    c = db.conn()
    if dados.ativo is not None:
        c.execute("UPDATE usuarios SET ativo=? WHERE id=?", (int(dados.ativo), uid))
        if not dados.ativo:
            c.execute("DELETE FROM sessoes WHERE usuario_id=?", (uid,))
    if dados.admin is not None:
        c.execute("UPDATE usuarios SET papel=? WHERE id=?", ("admin" if dados.admin else "usuario", uid))
    c.commit()
    return {"ok": True}


@app.get("/api/admin/config")
def admin_config(request: Request, u=Depends(admin_atual)):
    return {"campos": configuracoes.listar_para_admin(), "webhook_url": _url_publica(request) + "/api/kiwify/webhook",
            "n8n_ok": automacao.configurada()}


class NovaConfig(BaseModel):
    valores: dict[str, str]


@app.put("/api/admin/config")
def admin_config_salvar(dados: NovaConfig, request: Request, u=Depends(admin_atual)):
    for k, v in dados.valores.items():
        if k not in configuracoes.CAMPOS:
            raise HTTPException(400, f"Configuração desconhecida: {k}")
        if configuracoes.CAMPOS[k][1] and not v.strip():
            continue  # segredo em branco = manter o atual (para apagar, use o botão "limpar")
        configuracoes.definir(k, v)
    return admin_config(request, u)


@app.post("/api/admin/config/testar-email")
def admin_testar_email(u=Depends(admin_atual)):
    """Manda o e-mail de boas-vindas para o próprio admin, para conferir remetente, senha de app e visual."""
    try:
        emails.enviar(emails.montar_mensagem(u["nome"], u["email"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "para": u["email"]}


@app.delete("/api/admin/config/{chave}")
def admin_config_limpar(chave: str, u=Depends(admin_atual)):
    try:
        configuracoes.definir(chave, "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/admin/eventos")
def admin_eventos(u=Depends(admin_atual)):
    return kiwify.eventos_recentes(40)


class Simulacao(BaseModel):
    email: str = Field(max_length=120)
    produto: str = Field(max_length=120)
    evento: str = Field(default="order_approved", max_length=40)


@app.post("/api/admin/kiwify/simular")
def admin_kiwify_simular(dados: Simulacao, u=Depends(admin_atual)):
    """Roda um evento de teste pelo MESMO caminho do webhook real (confere o vínculo produto → plano)."""
    return kiwify.processar({"webhook_event_type": dados.evento, "order_status": "paid" if "approved" in dados.evento else "",
                             "order_id": "SIMULADO", "Customer": {"email": dados.email, "full_name": "Teste"},
                             "Product": {"product_id": dados.produto, "product_name": dados.produto},
                             "Subscription": {"plan": {"id": dados.produto, "name": dados.produto}}})
