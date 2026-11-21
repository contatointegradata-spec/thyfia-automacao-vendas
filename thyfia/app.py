"""API e servidor web da ThyfIA.

Rodar:  .venv/bin/python -m uvicorn thyfia.app:app --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

# configuração local (N8N_WEBHOOK_URL, N8N_API_KEY…) — o .env fica fora do git
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agente_publicacao, auth, automacao, cerebro, db, imagem_host, instagram, itens, motor_ml

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "thyfia_sessao"
ATIVOS = ("iniciando", "coletando", "gerando")

db.init()
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

app = FastAPI(title="ThyfIA", docs_url=None, redoc_url=None)
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


class NovoJob(BaseModel):
    termo: str = Field(min_length=2, max_length=80)
    limite: int = Field(default=20, ge=1, le=50)


# --------------------------------------------------------------------------- helpers
def usuario_atual(request: Request):
    u = auth.usuario_da_sessao(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Faça login para continuar.")
    return u


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600,
                    secure=os.environ.get("THYFIA_COOKIE_SECURE") == "1")  # 1 = atrás de HTTPS (produção)


def _job_dict(r) -> dict:
    d = {k: r[k] for k in ("id", "termo", "limite", "status", "total", "feitos", "ok", "erro",
                           "criado", "finalizado", "envio_status", "envio_em", "envio_erro")}
    total, com_link = itens.contar(r["id"])
    d["itens"], d["links"] = total, com_link
    # a planilha é gerada do banco; o arquivo local é só uma cópia opcional
    d["planilha"] = r["status"] == "concluido" and (total > 0 or bool(r["arquivo"] and Path(r["arquivo"]).exists()))
    try:
        d["envio_resumo"] = json.loads(r["envio_resumo"]) if r["envio_resumo"] else None
    except ValueError:
        d["envio_resumo"] = None
    return d


def _job_ativo(uid: int):
    r = db.conn().execute(
        f"SELECT * FROM jobs WHERE usuario_id=? AND status IN ({','.join('?' * len(ATIVOS))}) "
        "ORDER BY criado DESC LIMIT 1", (uid, *ATIVOS)).fetchone()
    return _job_dict(r) if r else None


def _preferencias(uid: int) -> dict:
    r = db.conn().execute("SELECT auto_enviar FROM usuarios WHERE id=?", (uid,)).fetchone()
    return {"auto_enviar": bool(r and r["auto_enviar"]), "automacao_configurada": automacao.configurada()}


def _perfil_usuario(u) -> dict:
    return {"id": u["id"], "nome": u["nome"], "email": u["email"],
            "ml": motor_ml.status_ml(u["id"]), "job_ativo": _job_ativo(u["id"]),
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
    _set_cookie(resp, auth.criar_sessao(uid))
    return _perfil_usuario(auth.autenticar(dados.email, dados.senha))


@app.post("/api/login")
def login(dados: Login, resp: Response):
    u = auth.autenticar(dados.email, dados.senha)
    if not u:
        raise HTTPException(401, "E-mail ou senha incorretos.")
    _set_cookie(resp, auth.criar_sessao(u["id"]))
    return _perfil_usuario(u)


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
def ml_conectar(u=Depends(usuario_atual)):
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
def ml_sessao(dados: Sessao, u=Depends(usuario_atual)):
    """Recebe a sessão do Mercado Livre do navegador da pessoa (extensão ThyfIA Conector)."""
    try:
        motor_ml.salvar_sessao(u["id"], dados.cookies)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return motor_ml.status_ml(u["id"])


@app.get("/api/extensao")
def info_extensao():
    return {"pasta": str(Path(__file__).resolve().parent.parent / "extensao_thyfia")}


@app.post("/api/ml/desconectar")
def ml_desconectar(u=Depends(usuario_atual)):
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Aguarde a geração de links terminar.")
    motor_ml.desconectar(u["id"])
    return motor_ml.status_ml(u["id"])


# --------------------------------------------------------------------------- jobs
@app.post("/api/jobs")
def criar_job(dados: NovoJob, u=Depends(usuario_atual)):
    if motor_ml.status_ml(u["id"])["status"] != "conectado":
        raise HTTPException(400, "Conecte sua conta do Mercado Livre primeiro.")
    if _job_ativo(u["id"]):
        raise HTTPException(409, "Você já tem uma geração em andamento.")
    job_id = secrets.token_hex(8)
    termo = dados.termo.strip()
    c = db.conn()
    c.execute("INSERT INTO jobs (id, usuario_id, termo, limite) VALUES (?,?,?,?)",
              (job_id, u["id"], termo, dados.limite))
    c.commit()
    threading.Thread(target=motor_ml.executar_job, args=(job_id, u["id"], termo, dados.limite),
                     daemon=True).start()
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
def enviar_job(job_id: str, u=Depends(usuario_atual)):
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
    c = db.conn()
    c.execute("UPDATE usuarios SET auto_enviar=? WHERE id=?", (int(dados.auto_enviar), u["id"]))
    c.commit()
    return _preferencias(u["id"])


# --------------------------------------------------------------------------- Agente de Publicação
_cache_ig: dict = {"em": 0.0, "dados": None}


def _erro_pub(e: Exception):
    if isinstance(e, agente_publicacao.ErroPublicacao):
        return HTTPException(e.status, str(e))
    if isinstance(e, imagem_host.ErroImagem):
        return HTTPException(400, str(e))
    if isinstance(e, instagram.ErroInstagram):
        return HTTPException(503 if e.codigo == "config" else 502, str(e))
    raise e


@app.get("/api/instagram/status")
def instagram_status(atualizar: bool = False, u=Depends(usuario_atual)):
    """Configurado? @username? limite restante? (o token nunca sai do backend)"""
    base = {"agendador_ativo": agente_publicacao.agendador_ativo(), "aviso": agente_publicacao.AVISO_AGENDADOR,
            "url_publica": bool(imagem_host.url_base_publica())}
    if not instagram.configurado():
        return {**base, "configurado": False, "mensagem": instagram.NAO_CONFIGURADO}
    if not atualizar and _cache_ig["dados"] and time.time() - _cache_ig["em"] < 60:
        return {**base, **_cache_ig["dados"]}
    dados = {"configurado": True, "username": None, "host": None, "limite": None, "erro": None}
    try:
        conta = instagram.validar_conta()
        dados.update(username=conta["username"], host=conta["host"])
        dados["limite"] = instagram.limite_publicacao()
    except instagram.ErroInstagram as e:
        dados["erro"] = str(e)
    _cache_ig.update(em=time.time(), dados=dados)
    return {**base, **dados}


@app.post("/api/instagram/validar")
def instagram_validar(u=Depends(usuario_atual)):
    """Rota de teste: só chama validar_conta() (não publica nada)."""
    try:
        return instagram.validar_conta()
    except instagram.ErroInstagram as e:
        raise _erro_pub(e)


class Sugerir(BaseModel):
    keyword: str | None = Field(default=None, max_length=80)
    job_id: str | None = Field(default=None, max_length=32)
    quantidade: int = Field(default=5, ge=1, le=20)


class NovaPublicacao(BaseModel):
    item_id: int | None = None          # None = post manual (foto enviada)
    legenda: str = Field(max_length=2200)
    image_url: str = Field(max_length=2000)
    agendado_para: str | None = Field(default=None, max_length=40)
    confirmar: bool = False


@app.post("/api/publicacao/sugerir")
def publicacao_sugerir(dados: Sugerir, u=Depends(usuario_atual)):
    return agente_publicacao.sugerir(u["id"], dados.keyword, dados.job_id, dados.quantidade)


@app.get("/api/publicacao/palavras")
def publicacao_palavras(u=Depends(usuario_atual)):
    return {"palavras": agente_publicacao.palavras_chave(u["id"])}


@app.post("/api/publicacao/imagem")
async def publicacao_imagem(request: Request, u=Depends(usuario_atual)):
    """Foto enviada pela pessoa (corpo = bytes da imagem). Vira JPEG em dados/midia."""
    corpo = await request.body()
    try:
        return imagem_host.salvar_upload(corpo, db.MIDIA)
    except imagem_host.ErroImagem as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/agendar")
def publicacao_agendar(dados: NovaPublicacao, u=Depends(usuario_atual)):
    if not dados.agendado_para:
        raise HTTPException(400, "Escolha a data e a hora da publicação.")
    try:
        return agente_publicacao.agendar(u["id"], dados.item_id, dados.legenda, dados.image_url, dados.agendado_para)
    except Exception as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/publicar-agora")
def publicacao_publicar_agora(dados: NovaPublicacao, u=Depends(usuario_atual)):
    try:
        pub = agente_publicacao.publicar_agora(u["id"], dados.item_id, dados.legenda, dados.image_url, dados.confirmar)
    except Exception as e:
        raise _erro_pub(e)
    _cache_ig["em"] = 0  # o limite mudou
    return pub


@app.get("/api/publicacao")
def publicacao_listar(u=Depends(usuario_atual)):
    return agente_publicacao.listar(u["id"])


@app.put("/api/publicacao/{pub_id}")
def publicacao_editar(pub_id: int, dados: NovaPublicacao, u=Depends(usuario_atual)):
    """Altera uma publicação agendada (legenda, foto, data/hora)."""
    if not dados.agendado_para:
        raise HTTPException(400, "Escolha a data e a hora da publicação.")
    try:
        return agente_publicacao.editar(u["id"], pub_id, dados.legenda, dados.image_url, dados.agendado_para)
    except Exception as e:
        raise _erro_pub(e)


@app.post("/api/publicacao/{pub_id}/cancelar")
def publicacao_cancelar(pub_id: int, u=Depends(usuario_atual)):
    try:
        return agente_publicacao.cancelar(u["id"], pub_id)
    except Exception as e:
        raise _erro_pub(e)


# --------------------------------------------------------------------------- cérebro
@app.get("/api/cerebro/sugestoes")
def sugestoes(q: str = "", u=Depends(usuario_atual)):
    return {"sugestoes": cerebro.sugerir(q[:80]), "relacionados": cerebro.relacionados(q[:80]) if q else []}


@app.get("/api/cerebro/stats")
def stats(u=Depends(usuario_atual)):
    return cerebro.estatisticas()
