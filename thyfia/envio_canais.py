"""
Envio para os destinos do Piloto Automático — WhatsApp (grupo/canal, via Evolution API) e Telegram.

Dois jeitos de enviar:
  • WhatsApp do CLIENTE (preferido): ele lê um QR code e a ThyfIA cria uma instância própria dele na Evolution
    (`thyfia-u{id}`). Os grupos vêm da conta dele e ele escolhe onde o Piloto publica — sem código de confirmação.
  • Número da PLATAFORMA (instância do admin): o cliente adiciona esse número como administrador do grupo/canal
    e confirma um código de 4 dígitos. Serve para grupos fora do WhatsApp dele. Telegram usa sempre o bot da ThyfIA.
Cada envio é classificado para o Piloto saber o que fazer:
    config   → falta configurar (Evolution/Telegram) — para tudo e avisa o admin
    conexao  → instância desconectada / servidor fora — pausa e tenta mais tarde
    destino  → número fora do grupo, grupo/canal inexistente ou sem permissão — conta falha do destino
    limite   → o serviço pediu para esperar (Telegram 429) — adia
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

import requests

from . import configuracoes

TIMEOUT = 25


class ErroEnvio(Exception):
    def __init__(self, mensagem: str, tipo: str = "destino", espera_s: int = 0):
        super().__init__(mensagem)
        self.tipo, self.espera_s = tipo, espera_s


# --------------------------------------------------------------------------- WhatsApp (Evolution)
def _evolution(instancia: str | None = None) -> tuple[str, str, str]:
    """(url, chave, instância). `instancia` = a do próprio cliente (conectada por QR); vazio = a da ThyfIA."""
    url, chave = configuracoes.obter("evolution_url"), configuracoes.obter("evolution_apikey")
    inst = instancia or configuracoes.obter("evolution_instancia")
    if not (url and chave and inst):
        raise ErroEnvio("A Evolution API ainda não foi configurada pelo administrador.", "config")
    return url.rstrip("/"), chave, inst


def _chamar(metodo: str, url: str, chave: str, tolerar: tuple[int, ...] = (), **kw):
    try:
        r = requests.request(metodo, url, headers={"apikey": chave, "Content-Type": "application/json"},
                             timeout=TIMEOUT, **kw)
    except requests.RequestException:
        raise ErroEnvio("Não consegui falar com a Evolution API.", "conexao")
    if r.status_code in tolerar:
        return r
    if r.status_code in (401, 403):
        raise ErroEnvio("A Evolution API recusou a chave de acesso.", "config")
    if r.status_code >= 500:
        raise ErroEnvio("A Evolution API está com problema agora.", "conexao")
    if r.status_code >= 400:
        try:
            msg = json.dumps(r.json(), ensure_ascii=False)[:300]
        except Exception:
            msg = ""
        if re.search(r"connection closed|not connected|disconnected|qrcode", msg, re.I):
            raise ErroEnvio("O WhatsApp da ThyfIA está desconectado.", "conexao")
        raise ErroEnvio("O WhatsApp não aceitou o envio. Confira se o número da ThyfIA é administrador do "
                        "grupo/canal.", "destino")
    return r


def whatsapp_enviar(jid: str, texto: str, imagem: str | None = None, instancia: str | None = None) -> None:
    url, chave, inst = _evolution(instancia)
    base = f"{url}/message"
    ins = quote(inst, safe="")
    if imagem:
        try:
            _chamar("POST", f"{base}/sendMedia/{ins}", chave, data=json.dumps(
                {"number": jid, "mediatype": "image", "mimetype": "image/jpeg", "fileName": "oferta.jpg",
                 "media": imagem, "caption": texto}))
            return
        except ErroEnvio as e:
            if e.tipo != "destino":  # foto recusada (URL ruim) não impede de mandar só o texto
                raise
    _chamar("POST", f"{base}/sendText/{ins}", chave, data=json.dumps(
        {"number": jid, "text": texto, "linkPreview": True}))


def whatsapp_resolver_convite(entrada: str) -> dict:
    """Link chat.whatsapp.com/XXXX (ou o código) → {jid, nome}, sem entrar no grupo."""
    m = re.search(r"chat\.whatsapp\.com/([A-Za-z0-9]{10,40})", entrada) or re.fullmatch(r"([A-Za-z0-9]{16,40})", entrada.strip())
    if not m:
        raise ErroEnvio("Cole o link de convite do grupo (chat.whatsapp.com/…).", "destino")
    url, chave, inst = _evolution()
    r = _chamar("GET", f"{url}/group/inviteInfo/{quote(inst, safe='')}", chave, params={"inviteCode": m.group(1)})
    try:
        d = r.json()
    except ValueError:
        d = {}
    jid = d.get("id") or d.get("jid") or ""
    if not str(jid).endswith("@g.us"):
        raise ErroEnvio("Não encontrei esse grupo. Confira o link de convite.", "destino")
    return {"jid": jid, "nome": d.get("subject") or d.get("name") or ""}


def whatsapp_texto_privado(numero: str, texto: str, instancia: str | None = None) -> None:
    """Aviso/relatório ao dono, no WhatsApp pessoal dele."""
    whatsapp_enviar(re.sub(r"\D", "", numero), texto, None, instancia)


# --------------------------------------------------------------------------- WhatsApp do cliente (QR code)
def _json(r) -> dict | list:
    try:
        return r.json()
    except ValueError:
        return {}


def _qr(d) -> str:
    """Imagem do QR (data URL) a partir da resposta da Evolution, onde quer que ela venha."""
    if isinstance(d, list):
        d = d[0] if d else {}
    for cand in (d, d.get("qrcode") if isinstance(d.get("qrcode"), dict) else {}):
        b64 = cand.get("base64") or ""
        if b64:
            return b64 if b64.startswith("data:") else "data:image/png;base64," + b64
    return ""


def wa_estado(instancia: str) -> str:
    """conectado | conectando | desconectado | inexistente"""
    url, chave, inst = _evolution(instancia)
    r = _chamar("GET", f"{url}/instance/connectionState/{quote(inst, safe='')}", chave, tolerar=(404,))
    if r.status_code == 404:
        return "inexistente"
    d = _json(r)
    estado = ((d.get("instance") or {}).get("state") or d.get("state") or "") if isinstance(d, dict) else ""
    return {"open": "conectado", "connecting": "conectando"}.get(estado, "desconectado")


def wa_conectar(instancia: str) -> dict:
    """Cria a instância do cliente (se não existir) e devolve {estado, qr}. QR vazio se já conectado."""
    url, chave, inst = _evolution(instancia)
    estado = wa_estado(instancia)
    if estado == "conectado":
        return {"estado": "conectado", "qr": ""}
    qr = ""
    if estado == "inexistente":
        r = _chamar("POST", f"{url}/instance/create", chave, tolerar=(403, 409),
                    data=json.dumps({"instanceName": inst, "qrcode": True, "integration": "WHATSAPP-BAILEYS"}))
        qr = _qr(_json(r)) if r.status_code < 400 else ""
    if not qr:
        r = _chamar("GET", f"{url}/instance/connect/{quote(inst, safe='')}", chave)
        d = _json(r)
        if isinstance(d, dict) and ((d.get("instance") or {}).get("state") == "open"):
            return {"estado": "conectado", "qr": ""}
        qr = _qr(d)
    return {"estado": "conectando", "qr": qr}


def wa_numero(instancia: str) -> str:
    """Número (só dígitos) do WhatsApp conectado nesta instância, se a Evolution informar."""
    url, chave, inst = _evolution(instancia)
    r = _chamar("GET", f"{url}/instance/fetchInstances", chave, tolerar=(404,), params={"instanceName": inst})
    d = _json(r)
    if isinstance(d, list):
        d = d[0] if d else {}
    d = d.get("instance", d) if isinstance(d, dict) else {}
    jid = d.get("ownerJid") or d.get("owner") or ""
    return re.sub(r"\D", "", str(jid).split("@")[0].split(":")[0])


def wa_grupos(instancia: str) -> list[dict]:
    """Todos os grupos do WhatsApp conectado: [{jid, nome, tamanho}]."""
    url, chave, inst = _evolution(instancia)
    r = _chamar("GET", f"{url}/group/fetchAllGroups/{quote(inst, safe='')}", chave, params={"getParticipants": "false"})
    d = _json(r)
    if isinstance(d, dict):
        d = d.get("groups") or d.get("data") or []
    saida = []
    for g in d if isinstance(d, list) else []:
        jid = g.get("id") or ""
        if str(jid).endswith("@g.us"):
            saida.append({"jid": jid, "nome": g.get("subject") or g.get("name") or jid,
                          "tamanho": g.get("size") or len(g.get("participants") or []) or None})
    return sorted(saida, key=lambda x: x["nome"].lower())


def wa_desconectar(instancia: str) -> None:
    url, chave, inst = _evolution(instancia)
    _chamar("DELETE", f"{url}/instance/logout/{quote(inst, safe='')}", chave, tolerar=(400, 404))


# --------------------------------------------------------------------------- Telegram
def _tg(metodo: str, corpo: dict) -> None:
    tok = configuracoes.obter("telegram_bot_token")
    if not tok:
        raise ErroEnvio("O bot do Telegram ainda não foi configurado pelo administrador.", "config")
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/{metodo}", json=corpo, timeout=TIMEOUT)
    except requests.RequestException:
        raise ErroEnvio("Não consegui falar com o Telegram.", "conexao")
    if r.status_code == 429:
        try:
            espera = int(r.json().get("parameters", {}).get("retry_after", 30))
        except Exception:
            espera = 30
        raise ErroEnvio("O Telegram pediu para esperar um pouco.", "limite", espera)
    if r.status_code >= 500:
        raise ErroEnvio("O Telegram está com problema agora.", "conexao")
    if r.status_code >= 400:
        bot = configuracoes.obter("telegram_bot")
        raise ErroEnvio("O Telegram recusou o envio. Adicione o bot" + (f" {bot}" if bot else "")
                        + " como ADMINISTRADOR do canal (com permissão de publicar).", "destino")


def telegram_enviar(chat: str, html_texto: str, imagem: str | None, link: str) -> None:
    botao = {"inline_keyboard": [[{"text": "🛒 Ver oferta", "url": link}]]}
    if imagem:
        try:
            _tg("sendPhoto", {"chat_id": chat, "photo": imagem, "caption": html_texto[:1000],
                              "parse_mode": "HTML", "reply_markup": botao})
            return
        except ErroEnvio as e:
            if e.tipo != "destino":
                raise
    _tg("sendMessage", {"chat_id": chat, "text": html_texto[:4000], "parse_mode": "HTML",
                        "reply_markup": botao, "disable_web_page_preview": False})


def telegram_texto(chat: str, texto: str) -> None:
    _tg("sendMessage", {"chat_id": chat, "text": texto})
