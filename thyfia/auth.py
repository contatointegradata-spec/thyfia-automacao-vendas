"""Contas e sessões da ThyfIA (senhas com PBKDF2 + sal, sessões por token em cookie)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from . import db

DURACAO_SESSAO = timedelta(days=7)
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash(senha: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), 200_000).hex()


def criar_usuario(nome: str, email: str, senha: str) -> int:
    nome, email = nome.strip(), email.strip().lower()
    if len(nome) < 2:
        raise ValueError("Informe seu nome.")
    if not RE_EMAIL.match(email):
        raise ValueError("E-mail inválido.")
    if len(senha) < 6:
        raise ValueError("A senha precisa ter pelo menos 6 caracteres.")
    c = db.conn()
    if c.execute("SELECT 1 FROM usuarios WHERE email=?", (email,)).fetchone():
        raise ValueError("Já existe uma conta com esse e-mail.")
    sal = secrets.token_hex(16)
    cur = c.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, sal) VALUES (?,?,?,?)",
        (nome, email, _hash(senha, sal), sal),
    )
    c.commit()
    return cur.lastrowid


def autenticar(email: str, senha: str):
    row = db.conn().execute(
        "SELECT * FROM usuarios WHERE email=?", (email.strip().lower(),)
    ).fetchone()
    if row and hmac.compare_digest(row["senha_hash"], _hash(senha, row["sal"])):
        return row
    return None


def garantir_admin() -> None:
    """Define quem é o admin (dono da plataforma).
    1) THYFIA_ADMIN_EMAIL, se a conta existir; 2) se ainda não há nenhum admin, a conta mais antiga.
    Chamado na subida e a cada cadastro (assim o e-mail do dono vira admin ao se cadastrar)."""
    import os
    c = db.conn()
    email = (os.environ.get("THYFIA_ADMIN_EMAIL") or "").strip().lower()
    if email:
        c.execute("UPDATE usuarios SET papel='admin' WHERE email=?", (email,))
    elif not c.execute("SELECT 1 FROM usuarios WHERE papel='admin'").fetchone():
        c.execute("UPDATE usuarios SET papel='admin' WHERE id=(SELECT MIN(id) FROM usuarios)")
    c.commit()


def criar_sessao(usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expira = (datetime.now() + DURACAO_SESSAO).isoformat(timespec="seconds")
    c = db.conn()
    c.execute("INSERT INTO sessoes (token, usuario_id, expira) VALUES (?,?,?)", (token, usuario_id, expira))
    c.commit()
    return token


def usuario_da_sessao(token: str | None):
    if not token:
        return None
    return db.conn().execute(
        "SELECT u.* FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id "
        "WHERE s.token=? AND s.expira > ? AND u.ativo = 1",
        (token, datetime.now().isoformat(timespec="seconds")),
    ).fetchone()


def encerrar_sessao(token: str | None) -> None:
    if token:
        c = db.conn()
        c.execute("DELETE FROM sessoes WHERE token=?", (token,))
        c.commit()
