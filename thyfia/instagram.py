"""
Cliente da Graph API do Instagram (publicação de conteúdo no feed).

Configuração (.env — nunca vai para o código, para o log nem para o navegador):
    IG_ACCESS_TOKEN, IG_USER_ID
    IG_GRAPH_HOST     (padrão https://graph.facebook.com)
    IG_GRAPH_VERSION  (padrão v21.0)

Fluxo de um post: criar_container(image_url, legenda) → aguardar_container(id) → publicar(id).
A API NÃO agenda posts de feed — quem agenda é o ThyfIA (ver agente_publicacao.py).

Teste manual da conta (só lê, não publica nada):
    .venv/bin/python -m thyfia.instagram
"""

from __future__ import annotations

import logging
import os
import re
import time

import requests

log = logging.getLogger("thyfia.instagram")

HOST_PADRAO = "https://graph.facebook.com"
HOST_INSTAGRAM = "https://graph.instagram.com"  # contas com "Login do Instagram"
VERSAO_PADRAO = "v21.0"
TIMEOUT = 30
NAO_CONFIGURADO = "Instagram não configurado"

_host_ativo: str | None = None  # host que respondeu em validar_conta()


class ErroInstagram(Exception):
    """Erro legível para a interface (nunca contém o token).

    codigo: config | token | limite | imagem | rede | container | api
    """

    def __init__(self, mensagem: str, codigo: str = "api"):
        super().__init__(limpar(mensagem))
        self.codigo = codigo


# --------------------------------------------------------------------------- configuração
def config() -> dict:
    return {
        "token": (os.environ.get("IG_ACCESS_TOKEN") or "").strip(),
        "user_id": (os.environ.get("IG_USER_ID") or "").strip(),
        "host": ((os.environ.get("IG_GRAPH_HOST") or "").strip() or HOST_PADRAO).rstrip("/"),
        "versao": (os.environ.get("IG_GRAPH_VERSION") or "").strip() or VERSAO_PADRAO,
    }


def configurado() -> bool:
    c = config()
    return bool(c["token"] and c["user_id"])


def limpar(texto) -> str:
    """Remove o token de qualquer texto (mensagens de erro, URLs)."""
    s = str(texto or "")
    tok = config()["token"]
    if tok:
        s = s.replace(tok, "***")
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", s)


# --------------------------------------------------------------------------- HTTP
_CODIGOS = {190: "token", 102: "token", 463: "token", 467: "token",
            4: "limite", 17: "limite", 32: "limite", 613: "limite", 9: "limite",
            9004: "imagem", 36000: "imagem", 36001: "imagem", 36003: "imagem", 36004: "imagem"}
_DICAS = {
    "token": "O token do Instagram é inválido ou expirou — gere um novo e atualize IG_ACCESS_TOKEN no .env.",
    "limite": "Muitas chamadas ao Instagram em pouco tempo. Aguarde alguns minutos.",
    "imagem": "O Instagram não conseguiu usar a imagem (precisa ser um JPEG público, proporção entre 4:5 e 1.91:1).",
}


def _mensagem_api(corpo, status: int) -> tuple[str, str]:
    err = corpo.get("error") if isinstance(corpo, dict) else None
    if not isinstance(err, dict):
        return f"O Instagram respondeu com erro HTTP {status}.", "token" if status == 401 else "api"
    code, sub = err.get("code"), err.get("error_subcode")
    codigo = _CODIGOS.get(code) or _CODIGOS.get(sub) or ("token" if status == 401 else "api")
    texto = err.get("error_user_msg") or err.get("message") or "erro desconhecido"
    titulo = err.get("error_user_title")
    partes = [f"Instagram: {titulo + ' — ' if titulo else ''}{texto}"]
    if code is not None:
        partes.append(f"(código {code}{f'/{sub}' if sub else ''})")
    if codigo in _DICAS:
        partes.append("— " + _DICAS[codigo])
    return " ".join(partes), codigo


def _req(metodo: str, caminho: str, params: dict | None = None, host: str | None = None,
         timeout: int = TIMEOUT) -> dict:
    c = config()
    if not (c["token"] and c["user_id"]):
        raise ErroInstagram(NAO_CONFIGURADO + " (defina IG_ACCESS_TOKEN e IG_USER_ID no .env).", "config")
    url = f"{(host or _host_ativo or c['host']).rstrip('/')}/{c['versao']}/{caminho.lstrip('/')}"
    dados = {**(params or {}), "access_token": c["token"]}
    try:
        if metodo == "GET":
            r = requests.get(url, params=dados, timeout=timeout)
        else:  # token no corpo do POST, nunca na URL
            r = requests.post(url, data=dados, timeout=timeout)
    except requests.Timeout:
        raise ErroInstagram("O Instagram demorou demais para responder. Tente de novo.", "rede") from None
    except requests.RequestException as e:
        raise ErroInstagram(f"Sem conexão com o Instagram ({type(e).__name__}).", "rede") from None
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    if r.status_code >= 400 or (isinstance(corpo, dict) and "error" in corpo):
        msg, codigo = _mensagem_api(corpo, r.status_code)
        raise ErroInstagram(msg, codigo)
    if not isinstance(corpo, dict):
        raise ErroInstagram("Resposta inesperada do Instagram.", "api")
    return corpo


# --------------------------------------------------------------------------- API
def validar_conta() -> dict:
    """GET /{IG_USER_ID}?fields=id,username. Se falhar no host configurado, tenta o
    graph.instagram.com. Retorna {id, username, host, host_padrao}."""
    global _host_ativo
    c = config()
    hosts = [c["host"]] + ([HOST_INSTAGRAM] if c["host"] != HOST_INSTAGRAM else [])
    erros: list[ErroInstagram] = []
    for h in hosts:
        try:
            d = _req("GET", c["user_id"], {"fields": "id,username"}, host=h)
        except ErroInstagram as e:
            if e.codigo == "config":
                raise
            erros.append(e)
            continue
        _host_ativo = h
        if h != c["host"]:
            log.info("Instagram: a conta respondeu em %s (não em %s).", h, c["host"])
        return {"id": str(d.get("id", "")), "username": d.get("username", ""),
                "host": h, "host_padrao": h == c["host"]}
    detalhe = " | ".join(f"{h}: {e}" for h, e in zip(hosts, erros))
    raise ErroInstagram(f"Não consegui validar a conta do Instagram. {detalhe}", erros[-1].codigo)


def limite_publicacao() -> dict:
    """GET /{IG_USER_ID}/content_publishing_limit → {usado, total, restante} (janela de 24h)."""
    d = _req("GET", f"{config()['user_id']}/content_publishing_limit", {"fields": "config,quota_usage"})
    linha = (d.get("data") or [{}])[0]
    usado = int(linha.get("quota_usage") or 0)
    total = int((linha.get("config") or {}).get("quota_total") or 100)
    return {"usado": usado, "total": total, "restante": max(total - usado, 0)}


def criar_container(image_url: str, caption: str) -> str:
    """POST /{IG_USER_ID}/media → id do container."""
    d = _req("POST", f"{config()['user_id']}/media", {"image_url": image_url, "caption": caption})
    if not d.get("id"):
        raise ErroInstagram("O Instagram não devolveu o id do container.", "container")
    return str(d["id"])


def aguardar_container(container_id: str, intervalo: float = 3, maximo: float = 60,
                       dormir=None) -> str:
    """Consulta status_code até FINISHED (a cada 3s, no máx. 60s). Falha em ERROR/EXPIRED."""
    dormir = dormir or time.sleep
    esperado = 0.0
    while True:
        d = _req("GET", container_id, {"fields": "status_code,status"})
        st = (d.get("status_code") or "").upper()
        if st in ("FINISHED", "PUBLISHED"):
            return st
        if st in ("ERROR", "EXPIRED"):
            extra = f" ({d['status']})" if d.get("status") else ""
            raise ErroInstagram(f"O Instagram recusou a mídia: {st}{extra}.", "container")
        if esperado >= maximo:
            raise ErroInstagram(f"A mídia não ficou pronta em {int(maximo)}s (status {st or '?'}).", "container")
        dormir(intervalo)
        esperado += intervalo


def publicar(container_id: str) -> str:
    """POST /{IG_USER_ID}/media_publish → id da mídia publicada."""
    d = _req("POST", f"{config()['user_id']}/media_publish", {"creation_id": container_id})
    if not d.get("id"):
        raise ErroInstagram("O Instagram não confirmou a publicação.", "api")
    return str(d["id"])


def publicar_imagem(image_url: str, caption: str, dormir=None) -> str:
    """Fluxo completo: container → aguardar → publish. Retorna o id da mídia."""
    cid = criar_container(image_url, caption)
    aguardar_container(cid, dormir=dormir)
    return publicar(cid)


if __name__ == "__main__":  # teste manual: só valida a conta (não publica)
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    try:
        conta = validar_conta()
        print(f"Conta OK: @{conta['username']} (id {conta['id']}) via {conta['host']}"
              + ("" if conta["host_padrao"] else "  ← use este host em IG_GRAPH_HOST"))
        lim = limite_publicacao()
        print(f"Limite de publicação: {lim['usado']}/{lim['total']} usados nas últimas 24h")
    except ErroInstagram as e:
        print("ERRO:", e)
        raise SystemExit(1)
