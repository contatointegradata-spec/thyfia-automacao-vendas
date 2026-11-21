"""Webhook da Kiwify: libera, renova, atrasa, cancela e reembolsa planos sozinho.

Na Kiwify (Apps → Webhooks) cadastre:
    URL    https://<seu-domínio>/api/kiwify/webhook
    Token  o mesmo salvo em Admin → Configurações → "Token do webhook da Kiwify"
    Eventos: compra aprovada, compra reembolsada, chargeback, assinatura cancelada/atrasada/renovada
A Kiwify assina cada chamada (?signature= HMAC-SHA1 do corpo, usando o token). Chamada sem
assinatura válida é recusada e registrada.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta

from . import configuracoes, db, planos

log = logging.getLogger("thyfia.kiwify")

DIAS_CICLO = 31          # acesso concedido a cada pagamento aprovado/renovado
FOLGA_DIAS = 3           # folga sobre a próxima cobrança informada pela Kiwify


# --------------------------------------------------------------------------- assinatura
def assinatura_confere(corpo: bytes, recebida: str | None, token: str) -> bool:
    if not token or not recebida:
        return False
    recebida = recebida.strip().lower()
    candidatos = [corpo]
    try:  # a Kiwify assina o JSON "compacto": cobre diferenças de espaços/escape
        obj = json.loads(corpo.decode("utf-8"))
        candidatos.append(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        candidatos.append(json.dumps(obj, separators=(",", ":")).encode("utf-8"))
    except ValueError:
        pass
    for c in candidatos:
        for alg in (hashlib.sha1, hashlib.sha256):
            if hmac.compare_digest(hmac.new(token.encode(), c, alg).hexdigest(), recebida):
                return True
    return False


def token_na_url_confere(recebido: str | None, token: str) -> bool:
    return bool(token and recebido) and hmac.compare_digest(str(recebido), token)


# --------------------------------------------------------------------------- leitura do payload
def _g(d, *caminho, padrao=None):
    for k in caminho:
        if not isinstance(d, dict):
            return padrao
        d = d.get(k)
    return padrao if d is None else d


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def extrair(p: dict) -> dict:
    # a Kiwify às vezes embrulha tudo em "order"
    base = p.get("order") if isinstance(p.get("order"), dict) and "Customer" in p["order"] else p
    evento = _norm(base.get("webhook_event_type") or p.get("webhook_event_type") or p.get("event") or p.get("trigger"))
    return {
        "evento": evento,
        "status": _norm(base.get("order_status")),
        "email": _norm(_g(base, "Customer", "email") or _g(base, "customer", "email")),
        "nome": str(_g(base, "Customer", "full_name") or _g(base, "Customer", "first_name") or "").strip(),
        "produto_id": str(_g(base, "Product", "product_id") or "").strip(),
        "produto_nome": str(_g(base, "Product", "product_name") or "").strip(),
        "plano_id": str(_g(base, "Subscription", "plan", "id") or "").strip(),
        "plano_nome": str(_g(base, "Subscription", "plan", "name") or "").strip(),
        "pedido": str(base.get("order_id") or base.get("order_ref") or "").strip(),
        "assinatura_id": str(_g(base, "Subscription", "id") or base.get("subscription_id") or "").strip(),
        "proxima": _g(base, "Subscription", "next_payment") or _g(base, "Subscription", "customer_access", "next_payment"),
    }


def plano_do_produto(produto_id: str, produto_nome: str, plano_id: str = "", plano_nome: str = "") -> str | None:
    """Casa o pagamento da Kiwify com um plano da ThyfIA, pelo ID ou nome salvo em
    Admin → Configurações (aceita vários separados por vírgula).
    Primeiro olha o PLANO de assinatura (um produto com 3 planos); se não casar, olha o produto
    (um produto por plano)."""
    alvos_por_plano = {pl: [_norm(x) for x in configuracoes.obter(f"kiwify_produto_{pl}").split(",") if x.strip()]
                       for pl in planos.ORDEM}
    for ids in ((plano_id, plano_nome), (produto_id, produto_nome)):
        chaves = {_norm(x) for x in ids if _norm(x)}
        if not chaves:
            continue
        for pl in planos.ORDEM:
            if chaves & set(alvos_por_plano[pl]):
                return pl
    return None


def _data(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


EVENTOS_APROVADO = {"order_approved", "compra_aprovada", "subscription_renewed", "assinatura_renovada"}
EVENTOS_REEMBOLSO = {"order_refunded", "compra_reembolsada", "chargeback", "chargedback"}
EVENTOS_CANCELADO = {"subscription_canceled", "assinatura_cancelada", "subscription_cancelled"}
EVENTOS_ATRASADO = {"subscription_late", "assinatura_atrasada"}


def decidir(d: dict) -> str:
    ev, st = d["evento"], d["status"]
    if ev in EVENTOS_REEMBOLSO or st in ("refunded", "chargedback", "chargeback"):
        return "reembolsar"
    if ev in EVENTOS_CANCELADO:
        return "cancelar"
    if ev in EVENTOS_ATRASADO:
        return "atrasar"
    if ev in EVENTOS_APROVADO or st == "paid":
        return "ativar"
    return "ignorar"


def registrar(evento, email, produto, plano, resultado, detalhe="") -> None:
    c = db.conn()
    c.execute("INSERT INTO eventos_pagamento (evento, email, produto, plano, resultado, detalhe) VALUES (?,?,?,?,?,?)",
              (evento, email, produto, plano, resultado, detalhe[:300]))
    c.execute("DELETE FROM eventos_pagamento WHERE id NOT IN (SELECT id FROM eventos_pagamento ORDER BY id DESC LIMIT 500)")
    c.commit()


def processar(payload: dict) -> dict:
    """Aplica o evento. Nunca levanta: devolve {resultado, detalhe} (a Kiwify precisa de 200)."""
    d = extrair(payload if isinstance(payload, dict) else {})
    acao = decidir(d)
    rotulo = d["evento"] or d["status"] or "?"
    produto = d["produto_nome"] or d["produto_id"]
    if acao == "ignorar":
        registrar(rotulo, d["email"], produto, None, "ignorado", "Evento sem efeito no acesso.")
        return {"resultado": "ignorado"}
    if not d["email"]:
        registrar(rotulo, "", produto, None, "erro", "Payload sem e-mail do cliente.")
        return {"resultado": "erro", "detalhe": "sem e-mail"}
    plano = plano_do_produto(d["produto_id"], d["produto_nome"], d["plano_id"], d["plano_nome"])
    if not plano:
        registrar(rotulo, d["email"], produto, None, "erro",
                  f"Plano/produto não vinculado (plano da Kiwify: {d['plano_nome'] or '—'}; produto: {produto or '—'}). "
                  "Confira Admin → Configurações.")
        return {"resultado": "erro", "detalhe": "produto desconhecido"}
    try:
        if acao == "ativar":
            prox = _data(d["proxima"])
            ate = (prox + timedelta(days=FOLGA_DIAS)) if prox and prox > datetime.now() else \
                  datetime.now() + timedelta(days=DIAS_CICLO)
            planos.conceder(d["email"], plano, origem="kiwify", nome=d["nome"] or None, pedido=d["pedido"] or None,
                            assinatura=d["assinatura_id"] or None, valido_ate=ate.isoformat(timespec="seconds"))
        elif acao == "reembolsar":
            planos.marcar(d["email"], plano, "reembolsada")
        elif acao == "cancelar":
            planos.marcar(d["email"], plano, "cancelada")
        elif acao == "atrasar":
            planos.marcar(d["email"], plano, "atrasada")
    except Exception as e:  # nunca devolve erro à Kiwify (ela reenviaria em loop)
        log.exception("Falha ao aplicar evento da Kiwify")
        registrar(rotulo, d["email"], produto, plano, "erro", f"{type(e).__name__}")
        return {"resultado": "erro"}
    registrar(rotulo, d["email"], produto, plano, "aplicado", acao)
    log.info("Kiwify: %s → %s (%s)", d["email"], acao, plano)
    return {"resultado": "aplicado", "acao": acao, "plano": plano}


def eventos_recentes(limite: int = 30) -> list[dict]:
    return [dict(r) for r in db.conn().execute(
        "SELECT * FROM eventos_pagamento ORDER BY id DESC LIMIT ?", (limite,)).fetchall()]
