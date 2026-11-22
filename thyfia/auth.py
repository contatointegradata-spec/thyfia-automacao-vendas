"""Contas e sessões da ThyfIA (senhas com PBKDF2 + sal, sessões por token em cookie)."""

from __future__ import annotations

import hashlib
import logging
import hmac
import re
import secrets
from datetime import datetime, timedelta

from . import db

DURACAO_SESSAO = timedelta(days=7)
DURACAO_REDEFINICAO = timedelta(minutes=60)
MAX_PEDIDOS_REDEFINICAO_HORA = 3  # por conta: evita encher a caixa de entrada de alguém
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


log = logging.getLogger("thyfia.auth")


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


def garantir_admin(inicial: bool = False) -> None:
    """Define quem é o admin (dono da plataforma).
    1) THYFIA_ADMIN_EMAIL, se a conta existir; 2) se ainda não há nenhum admin, a conta mais antiga.
    Chamado na subida e a cada cadastro (assim o e-mail do dono vira admin ao se cadastrar).
    Na subida (inicial=True), se THYFIA_ADMIN_SENHA também estiver definida, a conta desse e-mail é
    criada (se não existir) ou tem a senha redefinida para esse valor, e fica ativa. Assim o dono
    sempre consegue entrar; depois de entrar, apague a senha do .env."""
    import os
    c = db.conn()
    email = (os.environ.get("THYFIA_ADMIN_EMAIL") or "").strip().lower()
    senha = os.environ.get("THYFIA_ADMIN_SENHA") or ""
    if email and senha and inicial:
        if len(senha) < 6:
            log.warning("THYFIA_ADMIN_SENHA ignorada: precisa ter pelo menos 6 caracteres.")
        elif not c.execute("SELECT 1 FROM usuarios WHERE email=?", (email,)).fetchone():
            try:
                criar_usuario("Administrador", email, senha)
            except ValueError as e:
                log.warning("Não foi possível criar o administrador: %s", e)
        else:
            sal = secrets.token_hex(16)
            c.execute("UPDATE usuarios SET senha_hash=?, sal=?, ativo=1 WHERE email=?", (_hash(senha, sal), sal, email))
            c.commit()
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


# --------------------------------------------------------------------------- esqueci a senha
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pedir_redefinicao(email: str):
    """Cria um token de redefinição de senha. Devolve (nome, email, token) ou None.

    None quando a conta não existe, está desativada ou já pediu demais na última hora. Quem chama
    responde igual nos dois casos, para ninguém descobrir quais e-mails têm conta. Só o hash do token
    é guardado; o token em si só existe no e-mail enviado.
    """
    email = (email or "").strip().lower()
    c = db.conn()
    u = c.execute("SELECT id, nome, email FROM usuarios WHERE email=? AND ativo=1", (email,)).fetchone()
    if not u:
        return None
    desde = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    n = c.execute("SELECT COUNT(*) FROM redefinicoes_senha WHERE usuario_id=? AND criado > ?", (u["id"], desde)).fetchone()[0]
    if n >= MAX_PEDIDOS_REDEFINICAO_HORA:
        log.warning("redefinição de senha: limite por hora atingido para uma conta")
        return None
    token = secrets.token_urlsafe(32)
    expira = (datetime.now() + DURACAO_REDEFINICAO).isoformat(timespec="seconds")
    c.execute("UPDATE redefinicoes_senha SET usado=1 WHERE usuario_id=? AND usado=0", (u["id"],))  # o link novo vale; os antigos não
    c.execute("INSERT INTO redefinicoes_senha (usuario_id, token_hash, expira) VALUES (?,?,?)", (u["id"], _hash_token(token), expira))
    c.commit()
    return u["nome"], u["email"], token


def redefinir_senha(token: str, nova_senha: str) -> str:
    """Troca a senha com um token válido (uso único, 60 min). Encerra todas as sessões da conta. Devolve o e-mail."""
    if len(nova_senha or "") < 6:
        raise ValueError("A senha precisa ter pelo menos 6 caracteres.")
    c = db.conn()
    r = c.execute(
        "SELECT r.id, r.usuario_id, u.email FROM redefinicoes_senha r JOIN usuarios u ON u.id = r.usuario_id "
        "WHERE r.token_hash=? AND r.usado=0 AND r.expira > ? AND u.ativo=1",
        (_hash_token((token or "").strip()), datetime.now().isoformat(timespec="seconds"))).fetchone()
    if not r:
        raise ValueError("Esse link não é mais válido. Peça um novo link de redefinição.")
    sal = secrets.token_hex(16)
    c.execute("UPDATE usuarios SET senha_hash=?, sal=? WHERE id=?", (_hash(nova_senha, sal), sal, r["usuario_id"]))
    c.execute("UPDATE redefinicoes_senha SET usado=1 WHERE usuario_id=?", (r["usuario_id"],))
    c.execute("DELETE FROM sessoes WHERE usuario_id=?", (r["usuario_id"],))  # quem estava logado (talvez um invasor) cai
    c.commit()
    return r["email"]
