"""Planos, recursos liberados por plano e a assinatura de cada cliente.

Plus     — gerador de links (busca, planilha, histórico, cérebro)
Pro      — Plus + envio automático para a automação (WhatsApp/Telegram)
Premium  — Pro + Agente de Publicação (Instagram)
O perfil `admin` (dono da plataforma) tem tudo liberado, sempre.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import db

PLANOS: dict[str, dict] = {
    "plus": {
        "id": "plus", "nome": "Plus", "preco": 59.90, "nivel": 1, "destaque": False,
        "resumo": "Gere links de afiliado em escala e baixe a planilha.",
        "recursos": [
            "Gerador de links do Mercado Livre",
            "Planilha pronta para baixar (.xlsx)",
            "Histórico completo das gerações",
            "Cérebro: sugestões que aprendem com a comunidade",
            "Central de Ajuda passo a passo",
        ],
        "nao_inclui": ["Envio automático para o WhatsApp/Telegram", "Agente de Publicação no Instagram"],
    },
    "pro": {
        "id": "pro", "nome": "Pro", "preco": 89.90, "nivel": 2, "destaque": True,
        "resumo": "Tudo do Plus + a automação que divulga as ofertas por você.",
        "recursos": [
            "Tudo do plano Plus",
            "Envio automático dos links para a automação (n8n)",
            "Divulgação sorteada por categoria no seu WhatsApp e Telegram",
            "Configuração guiada do seu número, feita em tela",
            "Reenvio seguro (links duplicados são ignorados)",
        ],
        "nao_inclui": ["Agente de Publicação no Instagram"],
    },
    "premium": {
        "id": "premium", "nome": "Premium", "preco": 129.90, "nivel": 3, "destaque": False,
        "resumo": "Tudo do Pro + o Agente de Publicação no Instagram.",
        "recursos": [
            "Tudo do plano Pro",
            "Agente de Publicação: escolhe o melhor item e monta o post",
            "Agendamento de posts no Instagram com prévia",
            "Passo a passo para ligar sua conta Instagram/Meta",
            "Prioridade no suporte",
        ],
        "nao_inclui": [],
    },
}
ORDEM = ("plus", "pro", "premium")

# recurso → plano mínimo
RECURSOS = {"gerar": "plus", "automacao": "pro", "agente": "premium"}
NOMES_RECURSO = {"gerar": "o gerador de links", "automacao": "o envio para a automação",
                 "agente": "o Agente de Publicação"}

STATUS_COM_ACESSO = ("ativa", "atrasada", "cancelada")  # cancelada = vale até `valido_ate`
CARENCIA_DIAS = 0


def nivel(plano: str | None) -> int:
    return PLANOS.get(plano or "", {}).get("nivel", 0)


def agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _vigente(r) -> bool:
    if r["status"] not in STATUS_COM_ACESSO:
        return False
    return bool(r["valido_ate"]) and r["valido_ate"] > agora_iso()


def assinaturas_do_email(email: str) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM assinaturas WHERE email=? ORDER BY id", ((email or "").lower(),)).fetchall()
    return [dict(r) for r in rows]


def e_admin(u) -> bool:
    try:
        return (u["papel"] or "usuario") == "admin"
    except (KeyError, TypeError, IndexError):
        return False


def situacao(u) -> dict:
    """Plano efetivo do usuário (o MAIOR com acesso vigente) e os detalhes para a tela."""
    if e_admin(u):
        return {"plano": "premium", "ativo": True, "status": "admin", "valido_ate": None,
                "origem": "admin", "admin": True, "assinaturas": []}
    subs = assinaturas_do_email(u["email"])
    vigentes = [s for s in subs if _vigente(s)]
    melhor = max(vigentes, key=lambda s: nivel(s["plano"]), default=None)
    # se não há vigente, mostra a assinatura mais recente (para explicar o motivo na tela)
    ultima = melhor or (max(subs, key=lambda s: s["atualizado"], default=None))
    return {
        "plano": melhor["plano"] if melhor else None,
        "ativo": bool(melhor),
        "status": (melhor or ultima or {}).get("status") if (melhor or ultima) else "sem_plano",
        "valido_ate": (melhor or ultima or {}).get("valido_ate") if (melhor or ultima) else None,
        "origem": (melhor or ultima or {}).get("origem") if (melhor or ultima) else None,
        "ultimo_plano": ultima["plano"] if ultima else None,
        "admin": False,
        "assinaturas": [{k: s[k] for k in ("plano", "status", "valido_ate", "origem")} for s in subs],
    }


def plano_do(u) -> str | None:
    return situacao(u)["plano"]


def tem_recurso(u, recurso: str) -> bool:
    return nivel(plano_do(u)) >= nivel(RECURSOS[recurso])


def uid_tem_recurso(uid: int, recurso: str) -> bool:
    r = db.conn().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    return bool(r) and tem_recurso(r, recurso)


def liberados(u) -> dict[str, bool]:
    return {k: tem_recurso(u, k) for k in RECURSOS}


def catalogo() -> list[dict]:
    return [PLANOS[i] for i in ORDEM]


# --------------------------------------------------------------------------- escrita
def conceder(email: str, plano: str, dias: int = 31, origem: str = "manual", nome: str | None = None,
             pedido: str | None = None, assinatura: str | None = None, observacao: str | None = None,
             valido_ate: str | None = None) -> None:
    """Cria/renova a assinatura (e-mail, plano) como `ativa`."""
    if plano not in PLANOS:
        raise ValueError("Plano inválido.")
    email = email.strip().lower()
    ate = valido_ate or (datetime.now() + timedelta(days=dias)).isoformat(timespec="seconds")
    c = db.conn()
    c.execute(
        "INSERT INTO assinaturas (email, plano, status, valido_ate, origem, nome, kiwify_pedido, kiwify_assinatura, observacao) "
        "VALUES (?,?,'ativa',?,?,?,?,?,?) "
        "ON CONFLICT(email, plano) DO UPDATE SET status='ativa', valido_ate=excluded.valido_ate, "
        "origem=excluded.origem, nome=COALESCE(excluded.nome, nome), "
        "kiwify_pedido=COALESCE(excluded.kiwify_pedido, kiwify_pedido), "
        "kiwify_assinatura=COALESCE(excluded.kiwify_assinatura, kiwify_assinatura), "
        "observacao=COALESCE(excluded.observacao, observacao), atualizado=datetime('now','localtime')",
        (email, plano, ate, origem, nome, pedido, assinatura, observacao))
    c.commit()


def marcar(email: str, plano: str | None, status: str) -> int:
    """Muda o status (cancelada | atrasada | reembolsada | ativa) de uma assinatura; se plano=None,
    de todas do e-mail. Reembolso/chargeback também zera a validade (corta o acesso na hora)."""
    email = email.strip().lower()
    c = db.conn()
    extra = ", valido_ate=?" if status == "reembolsada" else ""
    args = [status] + ([agora_iso()] if extra else []) + [email]
    sql = f"UPDATE assinaturas SET status=?{extra}, atualizado=datetime('now','localtime') WHERE email=?"
    if status == "cancelada":  # a Kiwify manda "cancelada" logo depois de um reembolso: não desfaz o reembolso
        sql += " AND status!='reembolsada'"
    if plano:
        sql += " AND plano=?"
        args.append(plano)
    cur = c.execute(sql, args)
    c.commit()
    return cur.rowcount


def revogar(email: str, plano: str) -> None:
    db.conn().execute("DELETE FROM assinaturas WHERE email=? AND plano=? AND origem='manual'",
                      (email.strip().lower(), plano))
    db.conn().commit()
