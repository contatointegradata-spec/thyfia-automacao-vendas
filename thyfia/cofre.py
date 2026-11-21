"""Cofre de credenciais: cifra tokens dos clientes (ex.: token do Instagram) antes de gravar no banco.

A chave vem de THYFIA_SECRET_KEY ou, se não existir, é gerada uma vez e guardada em dados/.chave_cofre
(permissão 600, fora do git, no mesmo volume do banco). Sem a chave, os tokens gravados não abrem.
"""

from __future__ import annotations

import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from . import db

_lock = threading.Lock()
_fernet: Fernet | None = None


def _chave() -> bytes:
    env = (os.environ.get("THYFIA_SECRET_KEY") or "").strip()
    if env:
        return env.encode()
    arq = db.DADOS / ".chave_cofre"
    if arq.exists():
        return arq.read_bytes().strip()
    db.DADOS.mkdir(parents=True, exist_ok=True)
    k = Fernet.generate_key()
    fd = os.open(arq, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k)
    return k


def _f() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_chave())
        return _fernet


def cifrar(texto: str) -> str:
    return _f().encrypt(texto.encode("utf-8")).decode("ascii")


def abrir(cifrado: str | None) -> str:
    if not cifrado:
        return ""
    try:
        return _f().decrypt(cifrado.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mascarar(texto: str | None, visiveis: int = 4) -> str:
    t = texto or ""
    return "•" * 8 + t[-visiveis:] if len(t) > visiveis else ("•" * len(t))
