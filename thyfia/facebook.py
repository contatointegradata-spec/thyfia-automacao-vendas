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


def validar_pagina(page_id: str, token: str) -> dict:
    """Pergunta à Meta quem é a Página dona do token. → {id, nome}. Levanta ErroFacebook em português."""
    try:
        r = requests.get(f"{HOST}/{VERSAO}/{page_id}", params={"fields": "id,name", "access_token": token},
                         timeout=TIMEOUT)
    except requests.Timeout:
        raise ErroFacebook("O Facebook demorou demais para responder. Tente de novo.") from None
    except requests.RequestException:
        raise ErroFacebook("Sem conexão com o Facebook agora. Tente de novo.") from None
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    err = corpo.get("error") if isinstance(corpo, dict) else None
    if r.status_code >= 400 or err:
        code = (err or {}).get("code")
        if code in (190, 102, 463, 467) or r.status_code == 401:
            raise ErroFacebook("O token da Página é inválido ou expirou. Gere um token novo da Página e cole de novo.")
        if code in (100, 803, 10, 200):
            raise ErroFacebook("Não encontrei essa Página com esse token. Confira o ID da Página e se o token é "
                               "da Página (não do perfil).")
        msg = _limpar((err or {}).get("message") or f"HTTP {r.status_code}", token)
        raise ErroFacebook(f"O Facebook recusou: {msg}")
    if not isinstance(corpo, dict) or not corpo.get("id"):
        raise ErroFacebook("Resposta inesperada do Facebook.")
    return {"id": str(corpo["id"]), "nome": corpo.get("name") or ""}
