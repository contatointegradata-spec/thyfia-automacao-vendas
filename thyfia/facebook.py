"""
Conexão com a Página do Facebook do cliente (ID da Página + token da Página).

O token do Instagram não serve para o Facebook: a Página tem o seu próprio token. Aqui só validamos
a conexão (a publicação no Facebook vem na etapa seguinte). O token nunca aparece em erro nem em log.
"""

from __future__ import annotations

import re

import requests

HOST = "https://graph.facebook.com"
VERSAO = "v21.0"
TIMEOUT = 25


class ErroFacebook(Exception):
    pass


def _limpar(texto, token: str) -> str:
    s = str(texto or "")
    if token:
        s = s.replace(token, "***")
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", s)


def _erro(r, token: str) -> ErroFacebook:
    """Resposta de erro do Graph → mensagem em português (sem token)."""
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    err = corpo.get("error") if isinstance(corpo, dict) else None
    code = (err or {}).get("code")
    if code in (190, 102, 463, 467) or r.status_code == 401:
        return ErroFacebook("O token da Página é inválido ou expirou. Gere um token novo da Página e cole de novo.")
    if code in (200, 10, 283):
        return ErroFacebook("O token não tem permissão para publicar na Página. Gere o token de novo marcando "
                            "pages_manage_posts e pages_read_engagement.")
    if code in (100, 803):
        return ErroFacebook("Não encontrei essa Página com esse token. Confira o ID da Página e se o token é "
                            "da Página (não do perfil).")
    msg = _limpar((err or {}).get("message") or f"HTTP {r.status_code}", token)
    return ErroFacebook(f"O Facebook recusou: {msg}")


def _chamar(metodo: str, caminho: str, token: str, dados: dict) -> dict:
    try:
        if metodo == "GET":
            r = requests.get(f"{HOST}/{VERSAO}/{caminho}", params={**dados, "access_token": token}, timeout=TIMEOUT)
        else:
            r = requests.post(f"{HOST}/{VERSAO}/{caminho}", data={**dados, "access_token": token}, timeout=60)
    except requests.Timeout:
        raise ErroFacebook("O Facebook demorou demais para responder. Tente de novo.") from None
    except requests.RequestException:
        raise ErroFacebook("Sem conexão com o Facebook agora. Tente de novo.") from None
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    if r.status_code >= 400 or (isinstance(corpo, dict) and corpo.get("error")):
        raise _erro(r, token)
    if not isinstance(corpo, dict):
        raise ErroFacebook("Resposta inesperada do Facebook.")
    return corpo


def validar_pagina(page_id: str, token: str) -> dict:
    """Pergunta à Meta quem é a Página dona do token. → {id, nome}. Levanta ErroFacebook em português."""
    corpo = _chamar("GET", page_id, token, {"fields": "id,name"})
    if not corpo.get("id"):
        raise ErroFacebook("Resposta inesperada do Facebook.")
    return {"id": str(corpo["id"]), "nome": corpo.get("name") or ""}


# --------------------------------------------------------------------------- publicação
def publicar_foto(page_id: str, token: str, image_url: str, mensagem: str) -> str:
    """Foto com texto na linha do tempo da Página → id do post."""
    d = _chamar("POST", f"{page_id}/photos", token, {"url": image_url, "message": mensagem})
    post = d.get("post_id") or d.get("id")
    if not post:
        raise ErroFacebook("O Facebook não confirmou a publicação.")
    return str(post)


def publicar_link(page_id: str, token: str, mensagem: str, link: str) -> str:
    """Post de texto com link (o link é clicável e o Facebook monta a prévia) → id do post."""
    d = _chamar("POST", f"{page_id}/feed", token, {"message": mensagem, "link": link})
    if not d.get("id"):
        raise ErroFacebook("O Facebook não confirmou a publicação.")
    return str(d["id"])


def publicar_story_foto(page_id: str, token: str, image_url: str) -> str:
    """Story da Página: sobe a foto sem publicar e a transforma em story → id."""
    foto = _chamar("POST", f"{page_id}/photos", token, {"url": image_url, "published": "false"})
    if not foto.get("id"):
        raise ErroFacebook("O Facebook não recebeu a foto do story.")
    d = _chamar("POST", f"{page_id}/photo_stories", token, {"photo_id": foto["id"]})
    if d.get("success") is False:
        raise ErroFacebook("O Facebook não publicou o story.")
    return str(d.get("post_id") or d.get("id") or foto["id"])
