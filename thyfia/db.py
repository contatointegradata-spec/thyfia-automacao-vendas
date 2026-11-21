"""Banco SQLite da ThyfIA (usuários, sessões, jobs e a base de conhecimento)."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# THYFIA_DADOS permite apontar para outra pasta (usado pelos testes, para nunca tocar no banco real)
DADOS = Path(os.environ["THYFIA_DADOS"]) if os.environ.get("THYFIA_DADOS") else RAIZ / "dados"
DB_PATH = DADOS / "thyfia.db"
PLANILHAS = DADOS / "planilhas"
PERFIS = DADOS / "perfis"
MIDIA = DADOS / "midia"  # fotos enviadas pela pessoa (servidas em /midia para o Instagram buscar)

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    senha_hash  TEXT NOT NULL,
    sal         TEXT NOT NULL,
    criado      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sessoes (
    token       TEXT PRIMARY KEY,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_conexoes (
    usuario_id   INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'desconectado',  -- desconectado | conectando | conectado | erro
    mensagem     TEXT,
    atualizado   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    termo       TEXT NOT NULL,
    limite      INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'iniciando',      -- iniciando | coletando | gerando | concluido | erro
    total       INTEGER NOT NULL DEFAULT 0,
    feitos      INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 0,
    arquivo     TEXT,
    erro        TEXT,
    criado      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finalizado  TEXT
);

-- ===== Base de conhecimento coletiva (só dados públicos de produtos, nunca links de afiliado) =====
CREATE TABLE IF NOT EXISTS produtos (
    url          TEXT PRIMARY KEY,
    titulo       TEXT,
    titulo_norm  TEXT,
    preco        TEXT,
    vistos       INTEGER NOT NULL DEFAULT 0,
    gerados      INTEGER NOT NULL DEFAULT 0,
    falhas       INTEGER NOT NULL DEFAULT 0,
    atualizado   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS produto_termos (
    url    TEXT NOT NULL,
    termo  TEXT NOT NULL,
    vezes  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (url, termo)
);

CREATE TABLE IF NOT EXISTS termos (
    termo        TEXT PRIMARY KEY,
    buscas       INTEGER NOT NULL DEFAULT 0,
    links_ok     INTEGER NOT NULL DEFAULT 0,
    links_total  INTEGER NOT NULL DEFAULT 0,
    ultima       TEXT
);

-- ===== Itens de cada geração: a FONTE dos dados (envio ao n8n e download da planilha) =====
CREATE TABLE IF NOT EXISTS job_itens (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    busca          TEXT NOT NULL,          -- palavra-chave (coluna "Busca"; normalmente = jobs.termo)
    titulo         TEXT NOT NULL DEFAULT '',
    preco          REAL,                   -- número (ponto decimal); NULL se não deu para converter
    url_produto    TEXT NOT NULL DEFAULT '',
    link_afiliado  TEXT,                   -- NULL = link não gerado
    criado_em      TEXT NOT NULL           -- ISO 8601 com fuso (America/Sao_Paulo)
);
CREATE INDEX IF NOT EXISTS idx_itens_job ON job_itens(job_id);
-- mesmo link de afiliado não se repete no mesmo job (NULLs não conflitam)
CREATE UNIQUE INDEX IF NOT EXISTS uq_itens_job_link ON job_itens(job_id, link_afiliado);

-- ===== Agente de Publicação: posts do Instagram (o ThyfIA agenda; a API não agenda feed) =====
CREATE TABLE IF NOT EXISTS publicacoes_instagram (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        INTEGER REFERENCES job_itens(id) ON DELETE SET NULL,  -- NULL = post manual (foto enviada)
    usuario_id     INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'rascunho',  -- rascunho | agendado | publicando | publicado | erro | cancelado
    legenda        TEXT NOT NULL DEFAULT '',
    image_url      TEXT NOT NULL DEFAULT '',
    agendado_para  TEXT,                              -- ISO 8601 com fuso
    publicado_em   TEXT,
    ig_media_id    TEXT,
    erro           TEXT,
    criado_em      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_status ON publicacoes_instagram(status, agendado_para);
CREATE INDEX IF NOT EXISTS idx_pub_item ON publicacoes_instagram(item_id);

-- ===== Planos e assinaturas (Kiwify) =====
-- uma linha por (e-mail, plano): cancelar o Plus não derruba um Premium comprado depois
CREATE TABLE IF NOT EXISTS assinaturas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    email              TEXT NOT NULL,
    plano              TEXT NOT NULL,               -- plus | pro | premium
    status             TEXT NOT NULL,               -- ativa | atrasada | cancelada | reembolsada
    valido_ate         TEXT,                        -- ISO local; acesso até essa data
    origem             TEXT NOT NULL DEFAULT 'kiwify',  -- kiwify | manual
    nome               TEXT,
    kiwify_pedido      TEXT,
    kiwify_assinatura  TEXT,
    observacao         TEXT,
    criado             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    atualizado         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (email, plano)
);
CREATE INDEX IF NOT EXISTS idx_ass_email ON assinaturas(email);

CREATE TABLE IF NOT EXISTS eventos_pagamento (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recebido_em TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    evento      TEXT,
    email       TEXT,
    produto     TEXT,
    plano       TEXT,
    resultado   TEXT NOT NULL,                      -- aplicado | ignorado | recusado | erro
    detalhe     TEXT
);

-- configurações da plataforma (só o admin edita): Kiwify, n8n, Evolution…
CREATE TABLE IF NOT EXISTS configuracoes (
    chave      TEXT PRIMARY KEY,
    valor      TEXT NOT NULL,
    atualizado TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- configuração de CADA cliente (multi-tenant): canais de divulgação e Instagram/Meta
CREATE TABLE IF NOT EXISTS config_usuario (
    usuario_id        INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    whatsapp_numero   TEXT,        -- só dígitos com DDI (ex.: 5534999999999)
    whatsapp_status   TEXT NOT NULL DEFAULT 'nao_configurado',  -- nao_configurado | aguardando | conectado
    whatsapp_instancia TEXT,       -- nome da instância na Evolution (quando conectada pela ThyfIA)
    telegram_canal    TEXT,        -- @canal ou id
    ig_user_id        TEXT,
    ig_token_cifrado  TEXT,        -- NUNCA em texto puro
    ig_username       TEXT,
    ig_validado_em    TEXT,
    atualizado        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_usuario ON jobs(usuario_id, criado);
CREATE INDEX IF NOT EXISTS idx_pt_termo ON produto_termos(termo);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        DADOS.mkdir(exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


def init() -> None:
    for p in (DADOS, PLANILHAS, PERFIS, MIDIA):
        p.mkdir(parents=True, exist_ok=True)
    c = conn()
    c.executescript(SCHEMA)
    migracoes = (
        "ALTER TABLE ml_conexoes ADD COLUMN cookies TEXT",  # sessão do ML vinda da extensão
        # envio para a automação (n8n): nao_enviado | enviando | enviado | erro
        "ALTER TABLE jobs ADD COLUMN envio_status TEXT NOT NULL DEFAULT 'nao_enviado'",
        "ALTER TABLE jobs ADD COLUMN envio_em TEXT",
        "ALTER TABLE jobs ADD COLUMN envio_resumo TEXT",
        "ALTER TABLE jobs ADD COLUMN envio_erro TEXT",
        "ALTER TABLE usuarios ADD COLUMN auto_enviar INTEGER NOT NULL DEFAULT 0",
        # 1 = planilha antiga já lida para job_itens (lê uma única vez)
        "ALTER TABLE jobs ADD COLUMN itens_importados INTEGER NOT NULL DEFAULT 0",
        # foto do produto (URL do CDN do ML, capturada na busca) — usada pelo Agente de Publicação
        "ALTER TABLE job_itens ADD COLUMN imagem TEXT",
        # perfil: usuario | admin (o admin é o desenvolvedor/dono da plataforma)
        "ALTER TABLE usuarios ADD COLUMN papel TEXT NOT NULL DEFAULT 'usuario'",
        "ALTER TABLE usuarios ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1",
    )
    for sql in migracoes:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # coluna já existe
    # envios interrompidos pelo reinício do servidor
    c.execute("UPDATE jobs SET envio_status='erro', envio_erro='Envio interrompido (servidor reiniciado).' "
              "WHERE envio_status='enviando'")
    # publicação interrompida no meio: NÃO tenta de novo sozinho (pode já ter saído no Instagram)
    c.execute("UPDATE publicacoes_instagram SET status='erro', erro='Publicação interrompida (servidor "
              "reiniciado). Confira no Instagram antes de tentar de novo.' WHERE status='publicando'")
    # jobs que estavam rodando quando o servidor caiu
    c.execute(
        "UPDATE jobs SET status='erro', erro='Interrompido (o servidor foi reiniciado).' "
        "WHERE status IN ('iniciando','coletando','gerando')"
    )
    c.execute("UPDATE ml_conexoes SET status='desconectado' WHERE status='conectando'")
    c.commit()
