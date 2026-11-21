"""Configuração de cada cliente (multi-tenant): canais de divulgação e conta Instagram/Meta.

Tudo é feito em tela e fica no banco, por cliente. Tokens são cifrados (cofre) e nunca voltam
para o navegador. Nada disso depende do .env.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import datetime

import requests

from . import cofre, configuracoes, db, instagram, motor_ml, planos


class ErroConfig(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.status = status


# --------------------------------------------------------------------------- leitura
def _linha(uid: int):
    c = db.conn()
    r = c.execute("SELECT * FROM config_usuario WHERE usuario_id=?", (uid,)).fetchone()
    if not r:
        c.execute("INSERT OR IGNORE INTO config_usuario (usuario_id) VALUES (?)", (uid,))
        c.commit()
        r = c.execute("SELECT * FROM config_usuario WHERE usuario_id=?", (uid,)).fetchone()
    return r


def publica(uid: int) -> dict:
    """Visão segura para a tela (sem nenhum segredo)."""
    r = _linha(uid)
    return {
        "whatsapp_numero": r["whatsapp_numero"] or "",
        "whatsapp_formatado": formatar_numero(r["whatsapp_numero"]),
        "telegram_canal": r["telegram_canal"] or "",
        "instagram": {"configurado": bool(r["ig_user_id"] and r["ig_token_cifrado"]),
                      "user_id": r["ig_user_id"] or "", "username": r["ig_username"] or "",
                      "token": cofre.mascarar(cofre.abrir(r["ig_token_cifrado"])) if r["ig_token_cifrado"] else "",
                      "validado_em": r["ig_validado_em"]},
        "teste_whatsapp_disponivel": bool(configuracoes.obter("evolution_url") and configuracoes.obter("evolution_apikey")
                                          and configuracoes.obter("evolution_instancia")),
        "teste_telegram_disponivel": bool(configuracoes.obter("telegram_bot_token")),
        "bot_telegram": configuracoes.obter("telegram_bot"),
    }


# --------------------------------------------------------------------------- canais (Pro)
def normalizar_numero(bruto: str) -> str:
    d = re.sub(r"\D", "", bruto or "")
    if d.startswith("00"):
        d = d[2:]
    if len(d) in (10, 11):  # DDD + número (Brasil) → coloca o 55
        d = "55" + d
    if not 12 <= len(d) <= 15:
        raise ErroConfig("Número inválido. Use DDD + número, ex.: (34) 99999-9999.")
    if d.startswith("55") and len(d) not in (12, 13):
        raise ErroConfig("Número brasileiro inválido. Use DDD + número, ex.: (34) 99999-9999.")
    return d


def formatar_numero(n: str | None) -> str:
    if not n:
        return ""
    if n.startswith("55") and len(n) in (12, 13):
        l = n[4:]
        return f"+55 ({n[2:4]}) {l[:-4]}-{l[-4:]}"
    return "+" + n


def normalizar_canal(bruto: str) -> str:
    t = (bruto or "").strip()
    if not t:
        return ""
    t = re.sub(r"^https?://t\.me/", "@", t)
    if re.fullmatch(r"-?\d{6,20}", t):
        return t
    if not t.startswith("@"):
        t = "@" + t
    if not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", t):
        raise ErroConfig("Canal do Telegram inválido. Use o @ do canal, ex.: @minhasofertas.")
    return t


def salvar_canais(uid: int, whatsapp: str, telegram: str) -> dict:
    numero = normalizar_numero(whatsapp)
    canal = normalizar_canal(telegram)
    _linha(uid)
    c = db.conn()
    c.execute("UPDATE config_usuario SET whatsapp_numero=?, whatsapp_status='conectado', telegram_canal=?, "
              "atualizado=datetime('now','localtime') WHERE usuario_id=?", (numero, canal, uid))
    c.commit()
    return publica(uid)


def _e_admin_uid(uid: int) -> bool:
    r = db.conn().execute("SELECT papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    return bool(r and (r["papel"] or "usuario") == "admin")


def automacao_pronta(uid: int) -> bool:
    r = _linha(uid)
    if r["whatsapp_numero"] or r["telegram_canal"]:
        return True
    # o dono, sem número próprio, segue usando o destino padrão do n8n (como antes dos planos)
    return _e_admin_uid(uid)


def cliente_payload(uid: int) -> dict:
    """O que vai junto de cada envio ao n8n para ele saber PARA QUEM divulgar.
    Dono sem número/canal próprio: vazio (o n8n usa o destino padrão, sem mudar nada para ele)."""
    r = _linha(uid)
    if not (r["whatsapp_numero"] or r["telegram_canal"]) and _e_admin_uid(uid):
        return {}
    u = db.conn().execute("SELECT nome, email FROM usuarios WHERE id=?", (uid,)).fetchone()
    return {"id": uid, "nome": (u["nome"] if u else ""), "whatsapp": r["whatsapp_numero"] or "",
            "telegram": r["telegram_canal"] or "", "instancia": configuracoes.obter("evolution_instancia")}


def _erro_req(e: Exception) -> ErroConfig:
    return ErroConfig("Não consegui falar com o serviço agora. Tente de novo em instantes.", 502)


def testar_whatsapp(uid: int) -> dict:
    r = _linha(uid)
    if not r["whatsapp_numero"]:
        raise ErroConfig("Salve seu número de WhatsApp primeiro.")
    url, chave, inst = (configuracoes.obter(k) for k in ("evolution_url", "evolution_apikey", "evolution_instancia"))
    if not (url and chave and inst):
        raise ErroConfig("O teste de WhatsApp ainda não foi habilitado pelo administrador.", 503)
    try:
        resp = requests.post(f"{url.rstrip('/')}/message/sendText/{requests.utils.quote(inst, safe='')}",
                             headers={"apikey": chave, "Content-Type": "application/json"},
                             data=json.dumps({"number": r["whatsapp_numero"],
                                              "text": "✅ ThyfIA conectada! É assim que as suas ofertas vão chegar aqui."}),
                             timeout=20)
    except requests.RequestException as e:
        raise _erro_req(e)
    if resp.status_code >= 400:
        raise ErroConfig("O WhatsApp recusou o envio de teste. Confira se o número está certo e tente de novo.", 502)
    return {"ok": True}


def testar_telegram(uid: int) -> dict:
    r = _linha(uid)
    if not r["telegram_canal"]:
        raise ErroConfig("Salve o @ do seu canal do Telegram primeiro.")
    tok = configuracoes.obter("telegram_bot_token")
    if not tok:
        raise ErroConfig("O teste de Telegram ainda não foi habilitado pelo administrador.", 503)
    try:
        resp = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=20,
                             json={"chat_id": r["telegram_canal"],
                                   "text": "✅ ThyfIA conectada! As ofertas vão aparecer neste canal."})
    except requests.RequestException as e:
        raise _erro_req(e)
    if resp.status_code >= 400:
        bot = configuracoes.obter("telegram_bot")
        raise ErroConfig("Não consegui postar no canal. Adicione o bot" + (f" {bot}" if bot else "")
                         + " como ADMINISTRADOR do canal (com permissão de publicar) e tente de novo.", 502)
    return {"ok": True}


# --------------------------------------------------------------------------- Instagram / Meta (Premium)
def instagram_salvar(uid: int, user_id: str, token: str) -> dict:
    user_id = re.sub(r"\D", "", user_id or "")
    token = (token or "").strip()
    if not 8 <= len(user_id) <= 25:
        raise ErroConfig("O ID da conta do Instagram é só números (ex.: 17841450161230918).")
    if len(token) < 30 or re.search(r"\s", token):
        raise ErroConfig("Token inválido: cole o token inteiro, sem espaços ou quebras de linha.")
    # só grava se a Meta aceitar de verdade
    try:
        with instagram.credenciais(token, user_id):
            conta = instagram.validar_conta()
    except instagram.ErroInstagram as e:
        raise ErroConfig(str(e), 400)
    _linha(uid)
    c = db.conn()
    c.execute("UPDATE config_usuario SET ig_user_id=?, ig_token_cifrado=?, ig_username=?, ig_validado_em=?, "
              "atualizado=datetime('now','localtime') WHERE usuario_id=?",
              (user_id, cofre.cifrar(token), conta.get("username") or "", planos.agora_iso(), uid))
    c.commit()
    return publica(uid)["instagram"]


def instagram_remover(uid: int) -> dict:
    _linha(uid)
    c = db.conn()
    c.execute("UPDATE config_usuario SET ig_user_id=NULL, ig_token_cifrado=NULL, ig_username=NULL, "
              "ig_validado_em=NULL WHERE usuario_id=?", (uid,))
    c.commit()
    return publica(uid)["instagram"]


def _ig_env_liberado(uid: int) -> bool:
    """Credenciais do .env só valem para o admin (a conta do dono) ou no modo local de testes."""
    if os.environ.get("THYFIA_IG_ENV_FALLBACK") == "1":
        return True
    r = db.conn().execute("SELECT papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    return bool(r and r["papel"] == "admin")


def ig_ctx(uid: int):
    """Context manager: dentro dele, `instagram.*` usa a conta DESTE cliente."""
    r = db.conn().execute("SELECT * FROM config_usuario WHERE usuario_id=?", (uid,)).fetchone()
    token = cofre.abrir(r["ig_token_cifrado"]) if r else ""
    if token and r["ig_user_id"]:
        return instagram.credenciais(token, r["ig_user_id"])
    if _ig_env_liberado(uid):
        return contextlib.nullcontext()
    return instagram.credenciais("", "")  # não configurado: nunca cai na conta de outra pessoa


# --------------------------------------------------------------------------- passo a passo (checklist)
def checklist(u) -> list[dict]:
    """Passos do plano contratado, com o estado de cada um (para a tela guiada)."""
    uid = u["id"]
    sit = planos.situacao(u)
    r = _linha(uid)
    ml = motor_ml.status_ml(uid)["status"] == "conectado"
    passos = [
        {"id": "ml", "plano": "plus", "titulo": "Conectar o Mercado Livre",
         "feito": ml, "ir": "integracoes"},
        {"id": "gerar", "plano": "plus", "titulo": "Gerar seus primeiros links",
         "feito": bool(db.conn().execute("SELECT 1 FROM jobs WHERE usuario_id=? AND status='concluido'", (uid,)).fetchone()),
         "ir": "gerar"},
        {"id": "whatsapp", "plano": "pro", "titulo": "Informar seu WhatsApp",
         "feito": bool(r["whatsapp_numero"]), "ir": "automacao"},
        {"id": "telegram", "plano": "pro", "titulo": "Ligar seu canal do Telegram (opcional)",
         "feito": bool(r["telegram_canal"]), "ir": "automacao", "opcional": True},
        {"id": "envio", "plano": "pro", "titulo": "Enviar links para a automação",
         "feito": bool(db.conn().execute("SELECT 1 FROM jobs WHERE usuario_id=? AND envio_status='enviado'", (uid,)).fetchone()),
         "ir": "gerar"},
        {"id": "meta", "plano": "premium", "titulo": "Conectar Instagram/Meta",
         "feito": bool(r["ig_user_id"] and r["ig_token_cifrado"]), "ir": "publicacao"},
        {"id": "post", "plano": "premium", "titulo": "Agendar o primeiro post",
         "feito": bool(db.conn().execute("SELECT 1 FROM publicacoes_instagram WHERE usuario_id=?", (uid,)).fetchone()),
         "ir": "publicacao"},
    ]
    meu = planos.nivel(sit["plano"])
    return [p for p in passos if planos.nivel(p["plano"]) <= meu]
