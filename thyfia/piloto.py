"""
Piloto Automático — o Cérebro como motor da divulgação.

Fluxo (sem o cliente tocar no WhatsApp):
    ofertas do cliente (links já gerados) ──► CÉREBRO escolhe a melhor (curar_ofertas)
        ──► compositor escreve a mensagem (variada, sem inventar nada)
        ──► fila anti-bloqueio (janela de horário, intervalo, limite/dia, espaçamento aleatório)
        ──► WhatsApp (grupo/canal, pelo número da ThyfIA) e Telegram (bot da ThyfIA)
        ──► relatório diário e alertas para o dono, no WhatsApp dele.

Nichos: cada destino tem um nicho (fit, tech, casa, auto…; "geral" recebe tudo). A cada ciclo o Cérebro escolhe a
melhor oferta DE CADA NICHO que tem grupo e a manda só para os grupos daquele nicho.

O cliente (1) liga o Piloto, (2) conecta o WhatsApp dele por QR code e escolhe os grupos (ou adiciona o número/bot da
ThyfIA como admin e digita o código de confirmação) e (3) define o nicho de cada grupo.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta

from . import cerebro, configuracoes, db, envio_canais, imagem_host, mensagens, nichos, tenant
from .envio_canais import ErroEnvio
from .itens import TZ_SP

log = logging.getLogger("thyfia.piloto")

TIPOS = ("whatsapp_grupo", "whatsapp_canal", "telegram")
MAX_DESTINOS = 30
REPETICAO_DIAS = 14            # a mesma oferta não volta para o mesmo cliente antes disso
ESTOQUE_BAIXO = 5
FALHAS_PARA_DESLIGAR = 3
PAUSA_CONEXAO_MIN = 30
VALIDADE_ENVIO_H = 3           # envio que ficou na fila por mais que isso expira (preço envelhece)
ESPACO_DESTINOS_S = (8, 25)    # intervalo aleatório entre um destino e o próximo, no mesmo ciclo
GAP_GLOBAL_S = 6               # mínimo entre dois envios de WhatsApp saindo do mesmo número
HORA_RELATORIO = 20
INTERVALO_TICK = 20.0


class ErroPiloto(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.status = status


def _agora() -> datetime:
    return datetime.now(TZ_SP)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ_SP)


# --------------------------------------------------------------------------- configuração
def _linha_cfg(uid: int):
    c = db.conn()
    c.execute("INSERT OR IGNORE INTO piloto_config (usuario_id) VALUES (?)", (uid,))
    c.commit()
    return c.execute("SELECT * FROM piloto_config WHERE usuario_id=?", (uid,)).fetchone()


def config(uid: int) -> dict:
    r = dict(_linha_cfg(uid))
    try:
        r["palavras"] = [str(p) for p in json.loads(r["palavras"] or "[]")]
    except ValueError:
        r["palavras"] = []
    r["ativo"] = bool(r["ativo"])
    r["relatorio"] = bool(r["relatorio"])
    return r


def _num(v, lo, hi, padrao):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return padrao
    return max(lo, min(hi, n))


def _preco(v):
    if v in (None, ""):
        return None
    try:
        n = float(str(v).replace(",", "."))
    except ValueError:
        raise ErroPiloto("Preço inválido.")
    return n if n > 0 else None


def salvar_config(uid: int, d: dict) -> dict:
    atual = config(uid)
    d = {k: v for k, v in d.items() if v is not None or k in ("preco_min", "preco_max")}  # campo vazio = mantém
    ini = _num(d.get("hora_ini", atual["hora_ini"]), 0, 23, 8)
    fim = _num(d.get("hora_fim", atual["hora_fim"]), 1, 24, 21)
    if fim <= ini:
        raise ErroPiloto("O horário final precisa ser depois do inicial.")
    modo = d.get("modo", atual["modo"])
    if modo not in ("automatico", "aprovar"):
        raise ErroPiloto("Modo inválido.")
    estilo = d.get("estilo", atual["estilo"])
    if estilo not in mensagens.ESTILOS:
        raise ErroPiloto("Estilo de mensagem inválido.")
    pmin = _preco(d.get("preco_min", atual["preco_min"]))
    pmax = _preco(d.get("preco_max", atual["preco_max"]))
    if pmin and pmax and pmax < pmin:
        raise ErroPiloto("O preço máximo precisa ser maior que o mínimo.")
    palavras = d.get("palavras", atual["palavras"])
    if not isinstance(palavras, list):
        raise ErroPiloto("Palavras inválidas.")
    palavras = [re.sub(r"\s+", " ", str(p)).strip() for p in palavras][:30]
    palavras = [p for p in palavras if p]
    ativo = bool(d.get("ativo", atual["ativo"]))
    c = db.conn()
    c.execute(
        "UPDATE piloto_config SET ativo=?, modo=?, hora_ini=?, hora_fim=?, intervalo_min=?, max_dia=?, "
        "validade_dias=?, preco_min=?, preco_max=?, palavras=?, estilo=?, relatorio=?, "
        "atualizado=datetime('now','localtime') WHERE usuario_id=?",
        (int(ativo), modo, ini, fim, _num(d.get("intervalo_min", atual["intervalo_min"]), 10, 720, 30),
         _num(d.get("max_dia", atual["max_dia"]), 1, 60, 12),
         _num(d.get("validade_dias", atual["validade_dias"]), 1, 30, 7), pmin, pmax,
         json.dumps(palavras, ensure_ascii=False), estilo, int(bool(d.get("relatorio", atual["relatorio"]))), uid))
    if ativo and not atual["ativo"]:  # ao ligar: sai já no próximo ciclo e limpa pausa antiga
        c.execute("UPDATE piloto_config SET proximo_envio=NULL, pausa_ate=NULL, pausa_motivo=NULL "
                  "WHERE usuario_id=?", (uid,))
    c.commit()
    return config(uid)


# --------------------------------------------------------------------------- destinos
def _destino_publico(r) -> dict:
    return {"id": r["id"], "tipo": r["tipo"], "ref": r["ref"], "nome": r["nome"], "ativo": bool(r["ativo"]),
            "verificado": bool(r["verificado"]), "falhas": r["falhas"], "erro": r["erro"] or "",
            "experimental": r["tipo"] == "whatsapp_canal", "proprio": bool(r["instancia"]),
            "nicho": r["nicho"] or nichos.GERAL}


def listar_destinos(uid: int) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM piloto_destinos WHERE usuario_id=? ORDER BY id", (uid,)).fetchall()
    return [_destino_publico(r) for r in rows]


def _enviar_teste(tipo: str, ref: str, texto: str) -> None:
    if tipo == "telegram":
        envio_canais.telegram_texto(ref, texto)
    else:
        envio_canais.whatsapp_enviar(ref, texto)


def adicionar_destino(uid: int, tipo: str, entrada: str, nome: str = "", meu_numero: bool = False,
                      nicho: str = nichos.GERAL) -> dict:
    if tipo not in TIPOS:
        raise ErroPiloto("Tipo de destino inválido.")
    nicho = _nicho_ou_erro(nicho)
    entrada = (entrada or "").strip()
    if not entrada:
        raise ErroPiloto("Informe o destino.")
    c = db.conn()
    if c.execute("SELECT COUNT(*) FROM piloto_destinos WHERE usuario_id=?", (uid,)).fetchone()[0] >= MAX_DESTINOS:
        raise ErroPiloto(f"Limite de {MAX_DESTINOS} destinos atingido.")
    try:
        if tipo == "whatsapp_grupo":
            if entrada.endswith("@g.us"):
                ref = entrada
            else:
                info = envio_canais.whatsapp_resolver_convite(entrada)
                ref, nome = info["jid"], nome or info["nome"]
        elif tipo == "whatsapp_canal":
            if not re.fullmatch(r"\d{10,}@newsletter", entrada):
                raise ErroPiloto("Informe o ID do canal no formato 120363…@newsletter.")
            ref = entrada
        else:
            try:
                ref = tenant.normalizar_canal(entrada)
            except tenant.ErroConfig as e:
                raise ErroPiloto(str(e))
            if not ref:
                raise ErroPiloto("Informe o @ do canal do Telegram.")
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 502 if e.tipo in ("conexao", "config") else 400)

    if meu_numero and tipo == "whatsapp_canal":
        inst = _exigir_conectado(uid)
        c.execute("INSERT INTO piloto_destinos (usuario_id, tipo, ref, nome, verificado, instancia, nicho) "
                  "VALUES (?,?,?,?,1,?,?) ON CONFLICT(usuario_id, tipo, ref) DO UPDATE SET verificado=1, "
                  "instancia=excluded.instancia, ativo=1, nicho=excluded.nicho", (uid, tipo, ref, nome[:80], inst, nicho))
        c.commit()
        return _destino_publico(c.execute("SELECT * FROM piloto_destinos WHERE usuario_id=? AND tipo=? AND ref=?",
                                          (uid, tipo, ref)).fetchone())
    codigo = f"{random.randint(0, 9999):04d}"
    existente = c.execute("SELECT * FROM piloto_destinos WHERE usuario_id=? AND tipo=? AND ref=?",
                          (uid, tipo, ref)).fetchone()
    if existente and existente["verificado"]:
        raise ErroPiloto("Este destino já está ligado.", 409)
    try:
        _enviar_teste(tipo, ref, f"🔐 ThyfIA: seu código de confirmação é {codigo}. Digite-o no painel para "
                                 "ligar este destino. Se você não pediu isso, ignore esta mensagem.")
    except ErroEnvio as e:
        msg = str(e)
        if e.tipo == "destino" and tipo != "telegram":
            num = configuracoes.obter("evolution_numero")
            msg = ("Não consegui postar aí. Adicione o número da ThyfIA" + (f" (+{num})" if num else "")
                   + " ao grupo/canal como administrador e tente de novo.")
        raise ErroPiloto(msg, 502 if e.tipo in ("conexao", "config") else 400)
    if existente:
        c.execute("UPDATE piloto_destinos SET codigo=?, tentativas=0, nome=COALESCE(NULLIF(?,''),nome), "
                  "ativo=1, nicho=? WHERE id=?", (codigo, nome, nicho, existente["id"]))
        did = existente["id"]
    else:
        did = c.execute("INSERT INTO piloto_destinos (usuario_id, tipo, ref, nome, codigo, nicho) VALUES (?,?,?,?,?,?)",
                        (uid, tipo, ref, nome[:80], codigo, nicho)).lastrowid
    c.commit()
    return _destino_publico(c.execute("SELECT * FROM piloto_destinos WHERE id=?", (did,)).fetchone())


def confirmar_destino(uid: int, did: int, codigo: str) -> dict:
    c = db.conn()
    r = c.execute("SELECT * FROM piloto_destinos WHERE id=? AND usuario_id=?", (did, uid)).fetchone()
    if not r:
        raise ErroPiloto("Destino não encontrado.", 404)
    if r["verificado"]:
        return _destino_publico(r)
    if r["tentativas"] >= 5:
        raise ErroPiloto("Muitas tentativas. Remova o destino e adicione de novo para receber outro código.", 429)
    if (codigo or "").strip() != (r["codigo"] or "-"):
        c.execute("UPDATE piloto_destinos SET tentativas=tentativas+1 WHERE id=?", (did,))
        c.commit()
        raise ErroPiloto("Código incorreto. Confira a mensagem que chegou no grupo/canal.")
    c.execute("UPDATE piloto_destinos SET verificado=1, codigo=NULL, falhas=0, erro=NULL WHERE id=?", (did,))
    c.commit()
    return _destino_publico(c.execute("SELECT * FROM piloto_destinos WHERE id=?", (did,)).fetchone())


def remover_destino(uid: int, did: int) -> None:
    c = db.conn()
    if not c.execute("DELETE FROM piloto_destinos WHERE id=? AND usuario_id=?", (did, uid)).rowcount:
        raise ErroPiloto("Destino não encontrado.", 404)
    c.commit()


def alternar_destino(uid: int, did: int, ativo: bool) -> dict:
    c = db.conn()
    cur = c.execute("UPDATE piloto_destinos SET ativo=?, falhas=0, erro=NULL WHERE id=? AND usuario_id=?",
                    (int(ativo), did, uid))
    if not cur.rowcount:
        raise ErroPiloto("Destino não encontrado.", 404)
    c.commit()
    return _destino_publico(c.execute("SELECT * FROM piloto_destinos WHERE id=?", (did,)).fetchone())


def _nicho_ou_erro(nicho: str | None) -> str:
    nicho = (nicho or nichos.GERAL).strip().lower()
    if not nichos.valido(nicho):
        raise ErroPiloto("Nicho inválido.")
    return nicho


def definir_nicho(uid: int, did: int, nicho: str) -> dict:
    """Muda o nicho de um destino (as próximas ofertas já seguem o novo nicho)."""
    nicho = _nicho_ou_erro(nicho)
    c = db.conn()
    if not c.execute("UPDATE piloto_destinos SET nicho=? WHERE id=? AND usuario_id=?", (nicho, did, uid)).rowcount:
        raise ErroPiloto("Destino não encontrado.", 404)
    # o que ainda não saiu e é de outro nicho deixa de fazer sentido neste destino
    c.execute("UPDATE piloto_envios SET status='pulado', erro='Nicho do destino mudou.' WHERE destino_id=? "
              "AND status IN ('pendente','aguardando')", (did,))
    c.commit()
    return _destino_publico(c.execute("SELECT * FROM piloto_destinos WHERE id=?", (did,)).fetchone())


def _destinos_prontos(uid: int):
    return db.conn().execute(
        "SELECT * FROM piloto_destinos WHERE usuario_id=? AND ativo=1 AND verificado=1 ORDER BY id", (uid,)).fetchall()


# --------------------------------------------------------------------------- WhatsApp do cliente (QR code) e grupos
def instancia_do(uid: int) -> str:
    return f"thyfia-u{int(uid)}"


def _marcar_whatsapp(uid: int, estado: str, numero: str = "") -> None:
    tenant._linha(uid)
    c = db.conn()
    c.execute("UPDATE config_usuario SET whatsapp_instancia=?, whatsapp_status=? WHERE usuario_id=?",
              (instancia_do(uid), "conectado" if estado == "conectado" else "aguardando" if estado == "conectando"
               else "desconectado", uid))
    if numero:  # o dono ainda não informou número: usa o que acabou de conectar (recebe os avisos)
        c.execute("UPDATE config_usuario SET whatsapp_numero=? WHERE usuario_id=? AND COALESCE(whatsapp_numero,'')=''",
                  (numero, uid))
    c.commit()


def whatsapp_status(uid: int, atualizar: bool = False) -> dict:
    """Estado da conexão do WhatsApp do cliente. `atualizar` pergunta à Evolution (senão usa o último conhecido)."""
    r = tenant._linha(uid)
    estado = {"conectado": "conectado", "aguardando": "conectando"}.get(r["whatsapp_status"], "desconectado")
    if not r["whatsapp_instancia"]:
        estado = "nunca"
    erro = ""
    if atualizar and r["whatsapp_instancia"]:
        try:
            estado = envio_canais.wa_estado(instancia_do(uid))
            if estado == "inexistente":
                estado = "nunca"
            numero = ""
            if estado == "conectado":
                try:
                    numero = envio_canais.wa_numero(instancia_do(uid))
                except ErroEnvio:
                    pass
            if estado != "nunca":
                _marcar_whatsapp(uid, estado, numero)
        except ErroEnvio as e:
            erro = str(e)
    r = tenant._linha(uid)
    return {"estado": estado, "instancia": r["whatsapp_instancia"] or "", "numero": r["whatsapp_numero"] or "",
            "erro": erro}


def whatsapp_conectar(uid: int) -> dict:
    """Gera o QR code para o cliente ler (cria a instância dele na primeira vez)."""
    try:
        r = envio_canais.wa_conectar(instancia_do(uid))
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 503 if e.tipo == "config" else 502)
    numero = ""
    if r["estado"] == "conectado":
        try:
            numero = envio_canais.wa_numero(instancia_do(uid))
        except ErroEnvio:
            pass
    _marcar_whatsapp(uid, r["estado"], numero)
    if r["estado"] == "conectando" and not r["qr"]:
        raise ErroPiloto("O QR code ainda não ficou pronto. Tente de novo em alguns segundos.", 503)
    return {**r, "numero": tenant._linha(uid)["whatsapp_numero"] or ""}


def whatsapp_desconectar(uid: int) -> dict:
    try:
        envio_canais.wa_desconectar(instancia_do(uid))
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 502)
    _marcar_whatsapp(uid, "desconectado")
    return whatsapp_status(uid)


def _exigir_conectado(uid: int) -> str:
    inst = instancia_do(uid)
    try:
        estado = envio_canais.wa_estado(inst)
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 502)
    if estado != "conectado":
        raise ErroPiloto("Conecte o seu WhatsApp (QR code) primeiro.", 409)
    return inst


def grupos_whatsapp(uid: int) -> list[dict]:
    """Grupos do WhatsApp do cliente, com a marca dos que o Piloto já usa."""
    inst = _exigir_conectado(uid)
    try:
        grupos = envio_canais.wa_grupos(inst)
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 502)
    sel = {r["ref"]: r for r in db.conn().execute(
        "SELECT ref, ativo, nicho FROM piloto_destinos WHERE usuario_id=? AND tipo='whatsapp_grupo' AND instancia=?",
        (uid, inst))}
    return [{**g, "selecionado": g["jid"] in sel, "nicho": sel[g["jid"]]["nicho"] if g["jid"] in sel else nichos.GERAL}
            for g in grupos]


def salvar_grupos(uid: int, jids: list[str], nichos_por_grupo: dict[str, str] | None = None) -> list[dict]:
    """Define EXATAMENTE quais grupos do WhatsApp do cliente o Piloto usa (adiciona e remove) e o nicho de cada um.
    Grupo que já estava salvo mantém o nicho se o cliente não mandar outro."""
    nichos_por_grupo = {j: _nicho_ou_erro(n) for j, n in (nichos_por_grupo or {}).items()}
    inst = _exigir_conectado(uid)
    try:
        todos = {g["jid"]: g["nome"] for g in envio_canais.wa_grupos(inst)}
    except ErroEnvio as e:
        raise ErroPiloto(str(e), 502)
    jids = list(dict.fromkeys(jids))
    fora = [j for j in jids if j not in todos]  # só vale grupo que está mesmo no WhatsApp do cliente
    if fora:
        raise ErroPiloto("Algum grupo escolhido não está no seu WhatsApp. Atualize a lista e tente de novo.")
    c = db.conn()
    outros = c.execute("SELECT COUNT(*) FROM piloto_destinos WHERE usuario_id=? AND NOT (tipo='whatsapp_grupo' "
                       "AND instancia=?)", (uid, inst)).fetchone()[0]
    if outros + len(jids) > MAX_DESTINOS:
        raise ErroPiloto(f"O limite é de {MAX_DESTINOS} destinos no total.")
    marcas = ",".join("?" * len(jids)) or "''"
    c.execute(f"DELETE FROM piloto_destinos WHERE usuario_id=? AND tipo='whatsapp_grupo' AND instancia=? "
              f"AND ref NOT IN ({marcas})", (uid, inst, *jids))
    for j in jids:  # o grupo é do WhatsApp do cliente: dispensa o código de confirmação
        c.execute("INSERT INTO piloto_destinos (usuario_id, tipo, ref, nome, verificado, instancia, nicho) "
                  "VALUES (?,?,?,?,1,?,?) ON CONFLICT(usuario_id, tipo, ref) DO UPDATE SET nome=excluded.nome, "
                  "verificado=1, instancia=excluded.instancia, nicho=CASE WHEN ? THEN excluded.nicho "
                  "ELSE nicho END", (uid, "whatsapp_grupo", j, todos[j][:80], inst, nichos_por_grupo.get(j, nichos.GERAL),
                                    int(j in nichos_por_grupo)))
    c.commit()
    return listar_destinos(uid)


# --------------------------------------------------------------------------- curadoria (Cérebro)
_BLOQ = ("enviado", "pendente", "aguardando", "pulado")


def _bate_palavras(busca: str, palavras: list[str]) -> bool:
    if not palavras:
        return True
    b = cerebro.normalizar(busca)
    return any(cerebro.normalizar(p) and (cerebro.normalizar(p) in b or b in cerebro.normalizar(p)) for p in palavras)


def candidatos(uid: int, cfg: dict | None = None, agora: datetime | None = None, nicho: str | None = None,
               excluir: set[int] | None = None) -> list[dict]:
    """Ofertas do cliente que podem sair agora, já ranqueadas pelo Cérebro e marcadas com o nicho de cada uma.
    `nicho` restringe a um nicho (a diversificação de categorias então olha só o que saiu para esse nicho)."""
    cfg = cfg or config(uid)
    agora = agora or _agora()
    c = db.conn()
    corte = _iso(agora - timedelta(days=cfg["validade_dias"]))
    rows = [dict(r) for r in c.execute(
        "SELECT i.id, i.busca, i.titulo, i.preco, i.url_produto, i.link_afiliado, i.imagem, i.criado_em "
        "FROM job_itens i JOIN jobs j ON j.id = i.job_id "
        "WHERE j.usuario_id=? AND TRIM(i.titulo) <> '' AND i.preco IS NOT NULL AND i.preco > 0 "
        "AND i.link_afiliado LIKE 'http%' AND i.criado_em >= ? ORDER BY i.id DESC", (uid, corte)).fetchall()]
    rows = [r for r in rows if _bate_palavras(r["busca"], cfg["palavras"])
            and (not cfg["preco_min"] or r["preco"] >= cfg["preco_min"])
            and (not cfg["preco_max"] or r["preco"] <= cfg["preco_max"])]

    desde = _iso(agora - timedelta(days=REPETICAO_DIAS))
    recentes = c.execute(
        f"SELECT i.url_produto, i.link_afiliado FROM piloto_envios e JOIN job_itens i ON i.id = e.item_id "
        f"WHERE e.usuario_id=? AND e.status IN ({','.join('?' * len(_BLOQ))}) AND e.criado_em >= ?",
        (uid, *_BLOQ, desde)).fetchall()
    ja = {x for r in recentes for x in (r["url_produto"], r["link_afiliado"]) if x}
    vistos, unicos = set(), []
    for r in rows:  # mesmo produto em várias gerações conta uma vez só
        chave = r["url_produto"] or r["link_afiliado"]
        if chave in vistos or r["url_produto"] in ja or r["link_afiliado"] in ja:
            continue
        vistos.add(chave)
        r["nicho"] = nichos.classificar(r["busca"], r["titulo"])
        if (nicho and nicho != nichos.GERAL and r["nicho"] != nicho) or r["id"] in (excluir or ()):
            continue
        unicos.append(r)

    por_busca: dict[str, int] = {}
    for r in c.execute(
        "SELECT busca, COUNT(DISTINCT item_id) n FROM piloto_envios WHERE usuario_id=? AND status IN "
        f"({','.join('?' * len(_BLOQ))}) AND criado_em >= ? GROUP BY busca", (uid, *_BLOQ, _iso(agora - timedelta(days=7)))):
        k = cerebro.normalizar(r["busca"])
        por_busca[k] = por_busca.get(k, 0) + r["n"]
    if nicho and nicho != nichos.GERAL:
        ult = c.execute("SELECT e.busca FROM piloto_envios e JOIN piloto_destinos d ON d.id=e.destino_id "
                        "WHERE e.usuario_id=? AND d.nicho=? AND e.status<>'pulado' ORDER BY e.id DESC LIMIT 1",
                        (uid, nicho)).fetchone()
    else:
        ult = c.execute("SELECT busca FROM piloto_envios WHERE usuario_id=? AND status<>'pulado' "
                        "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    return cerebro.curar_ofertas(unicos, ultima_busca=ult["busca"] if ult else "", envios_por_busca=por_busca,
                                 validade_dias=cfg["validade_dias"], agora=agora)


# --------------------------------------------------------------------------- planejar e despachar
def _enviados_hoje(uid: int, agora: datetime) -> int:
    """Ofertas de hoje do destino que mais recebeu: o limite diário vale POR GRUPO (cada nicho tem o seu fluxo)."""
    r = db.conn().execute(
        "SELECT MAX(n) FROM (SELECT COUNT(DISTINCT item_id) n FROM piloto_envios WHERE usuario_id=? AND status IN "
        "('enviado','pendente','aguardando') AND substr(criado_em,1,10)=? GROUP BY destino_id)",
        (uid, _iso(agora)[:10])).fetchone()
    return r[0] or 0


def _avisar(uid: int, texto: str) -> None:
    """Mensagem ao dono, no WhatsApp pessoal dele (se ele informou o número). Nunca derruba o ciclo."""
    try:
        r = tenant._linha(uid)
        if r["whatsapp_numero"]:
            propria = r["whatsapp_instancia"] if r["whatsapp_status"] == "conectado" else None
            envio_canais.whatsapp_texto_privado(r["whatsapp_numero"], texto, propria)
    except Exception:
        log.warning("Piloto: não consegui avisar o cliente %s.", uid)


def _alertar_estoque(uid: int, agora: datetime, faltam: list[str] | None = None) -> None:
    hoje = _iso(agora)[:10]
    c = db.conn()
    if c.execute("SELECT alerta_estoque FROM piloto_config WHERE usuario_id=?", (uid,)).fetchone()[0] == hoje:
        return
    c.execute("UPDATE piloto_config SET alerta_estoque=? WHERE usuario_id=?", (hoje, uid))
    c.commit()
    quais = ", ".join(nichos.nome(n) for n in faltam or ())
    _avisar(uid, "📦 ThyfIA — Piloto Automático: acabaram as ofertas novas "
                 + (f"de {quais}" if quais else "para divulgar")
                 + ". Gere mais links no painel para o Piloto continuar enviando.")


def planejar(uid: int, agora: datetime | None = None, forcar: bool = False, host=None) -> dict:
    """Se for a hora, o Cérebro escolhe a melhor oferta e a fila recebe uma mensagem por destino."""
    agora = agora or _agora()
    cfg = config(uid)
    if not cfg["ativo"]:
        return {"acao": "desligado"}
    pausa = _dt(cfg["pausa_ate"])
    if pausa and agora < pausa:
        return {"acao": "pausado", "ate": cfg["pausa_ate"], "motivo": cfg["pausa_motivo"]}
    if not forcar:
        if not cfg["hora_ini"] <= agora.hour < cfg["hora_fim"]:
            return {"acao": "fora_do_horario"}
        prox = _dt(cfg["proximo_envio"])
        if prox and agora < prox:
            return {"acao": "aguardando_intervalo", "proximo": cfg["proximo_envio"]}
    destinos = _destinos_prontos(uid)
    if not destinos:
        return {"acao": "sem_destinos"}
    c = db.conn()
    if c.execute("SELECT 1 FROM piloto_envios WHERE usuario_id=? AND status IN ('pendente','aguardando') LIMIT 1",
                 (uid,)).fetchone():
        return {"acao": "ciclo_em_andamento"}
    if _enviados_hoje(uid, agora) >= cfg["max_dia"] and not forcar:
        return {"acao": "limite_do_dia"}

    # O Cérebro escolhe a melhor oferta de cada nicho que tem grupo; "geral" fica com a melhor das que sobraram.
    grupos: dict[str, list] = {}
    for d in destinos:
        grupos.setdefault(d["nicho"] or nichos.GERAL, []).append(d)
    ordem = [n for n in grupos if n != nichos.GERAL] + ([nichos.GERAL] if nichos.GERAL in grupos else [])
    escolhas, usadas, faltam = [], set(), []
    for n in ordem:
        pool = candidatos(uid, cfg, agora, nichos.GERAL if n == nichos.GERAL else n, usadas)
        if n == nichos.GERAL and not pool:
            pool = candidatos(uid, cfg, agora)  # nada sobrou: repete uma das já escolhidas para o grupo geral
        if not pool:
            faltam.append(n)
            continue
        escolhas.append((n, pool[0]))
        usadas.add(pool[0]["id"])
    if not escolhas:
        _alertar_estoque(uid, agora, faltam if set(faltam) - {nichos.GERAL} else None)
        c.execute("UPDATE piloto_config SET proximo_envio=? WHERE usuario_id=?",
                  (_iso(agora + timedelta(minutes=30)), uid))
        c.commit()
        return {"acao": "sem_estoque", "faltam": faltam}
    if set(faltam) - {nichos.GERAL}:  # parte dos nichos ficou sem oferta: avisa (no máx. 1x por dia)
        _alertar_estoque(uid, agora, [n for n in faltam if n != nichos.GERAL])
    status = "pendente" if cfg["modo"] == "automatico" else "aguardando"
    quando, total = agora, 0
    for n, escolhida in escolhas:
        try:
            imagem = (host or imagem_host.obter()).obter_url(escolhida)
        except Exception:
            imagem = None
        motivo = "; ".join(escolhida["motivos"]) or "melhor pontuação entre as ofertas disponíveis"
        if n != nichos.GERAL:
            motivo = f"nicho {nichos.nome(n)}; " + motivo
        for d in grupos[n]:
            m = mensagens.compor(escolhida, cfg["estilo"], d["id"])
            c.execute(
                "INSERT INTO piloto_envios (usuario_id, item_id, destino_id, status, busca, titulo, preco, texto, html, "
                "link, imagem, motivo, pontos, enviar_apos, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, escolhida["id"], d["id"], status, escolhida["busca"], m["titulo"], escolhida["preco"], m["texto"],
                 m["html"], escolhida["link_afiliado"], imagem, motivo, escolhida["pontos"],
                 _iso(quando) if status == "pendente" else None, _iso(agora)))
            quando += timedelta(seconds=random.randint(*ESPACO_DESTINOS_S))
            total += 1
    base = cfg["intervalo_min"] * 60
    prox = agora + timedelta(seconds=int(base * random.uniform(0.85, 1.15)))
    c.execute("UPDATE piloto_config SET proximo_envio=? WHERE usuario_id=?", (_iso(prox), uid))
    c.commit()
    primeira = escolhas[0][1]
    return {"acao": "planejou" if status == "pendente" else "aguardando_aprovacao", "item_id": primeira["id"],
            "titulo": primeira["titulo"], "motivo": "; ".join(primeira["motivos"]), "destinos": total,
            "ofertas": [{"nicho": n, "item_id": o["id"], "titulo": o["titulo"]} for n, o in escolhas],
            "faltam": faltam}


def _pausar(uid: int, agora: datetime, minutos: int, motivo: str) -> None:
    db.conn().execute("UPDATE piloto_config SET pausa_ate=?, pausa_motivo=? WHERE usuario_id=?",
                      (_iso(agora + timedelta(minutes=minutos)), motivo, uid))
    db.conn().commit()


_gate = threading.Lock()
_ultimo_wa: dict[str, float] = {}


def despachar(uid: int, agora: datetime | None = None) -> dict:
    """Envia, no máximo, UMA mensagem da fila por chamada (o ritmo vem do `enviar_apos` de cada uma)."""
    agora = agora or _agora()
    c = db.conn()
    # envio que esperou demais na fila não sai mais: o preço já pode ter mudado
    c.execute("UPDATE piloto_envios SET status='pulado', erro='Expirou na fila.' WHERE usuario_id=? "
              "AND status='pendente' AND criado_em < ?", (uid, _iso(agora - timedelta(hours=VALIDADE_ENVIO_H))))
    c.commit()
    cfg = config(uid)
    pausa = _dt(cfg["pausa_ate"])
    if pausa and agora < pausa:
        return {"acao": "pausado"}
    e = c.execute(
        "SELECT e.*, d.tipo, d.ref, d.ativo d_ativo, d.nome d_nome, d.instancia FROM piloto_envios e "
        "JOIN piloto_destinos d ON d.id = e.destino_id WHERE e.usuario_id=? AND e.status='pendente' "
        "AND e.enviar_apos <= ? ORDER BY e.id LIMIT 1", (uid, _iso(agora))).fetchone()
    if not e:
        return {"acao": "nada"}
    if not e["d_ativo"]:
        c.execute("UPDATE piloto_envios SET status='pulado', erro='Destino desligado.' WHERE id=?", (e["id"],))
        c.commit()
        return {"acao": "pulado"}
    whats = e["tipo"] != "telegram"
    with _gate:
        chave_gap = e["instancia"] or "plataforma"
        if whats and time.monotonic() - _ultimo_wa.get(chave_gap, 0.0) < GAP_GLOBAL_S:
            return {"acao": "aguardando_gap"}
        try:
            if whats:
                envio_canais.whatsapp_enviar(e["ref"], e["texto"], e["imagem"], e["instancia"])
                _ultimo_wa[chave_gap] = time.monotonic()
            else:
                envio_canais.telegram_enviar(e["ref"], e["html"], e["imagem"], e["link"])
        except ErroEnvio as err:
            return _tratar_erro(uid, e, err, agora)
    c.execute("UPDATE piloto_envios SET status='enviado', enviado_em=?, erro=NULL WHERE id=?", (_iso(agora), e["id"]))
    c.execute("UPDATE piloto_destinos SET falhas=0, erro=NULL WHERE id=?", (e["destino_id"],))
    c.execute("UPDATE piloto_config SET ultimo_envio=? WHERE usuario_id=?", (_iso(agora), uid))
    c.commit()
    return {"acao": "enviado", "envio_id": e["id"], "destino": e["d_nome"] or e["ref"]}


def _tratar_erro(uid: int, e, err: ErroEnvio, agora: datetime) -> dict:
    c = db.conn()
    log.warning("Piloto %s: envio %s falhou (%s): %s", uid, e["id"], err.tipo, err)
    if err.tipo == "limite":
        c.execute("UPDATE piloto_envios SET enviar_apos=? WHERE id=?",
                  (_iso(agora + timedelta(seconds=max(err.espera_s, 5) + 2)), e["id"]))
        c.commit()
        return {"acao": "adiado", "espera_s": err.espera_s}
    if err.tipo in ("conexao", "config"):  # problema de conexão/configuração: pausa e tenta depois
        if e["instancia"] and err.tipo == "conexao":
            _marcar_whatsapp(uid, "desconectado")
            _avisar(uid, "⚠️ ThyfIA — o seu WhatsApp saiu do ar e o Piloto pausou. Abra o painel, vá em "
                         "Piloto Automático e leia o QR code de novo.")
        _pausar(uid, agora, PAUSA_CONEXAO_MIN, str(err))
        return {"acao": "pausado", "motivo": str(err)}
    c.execute("UPDATE piloto_envios SET status='erro', erro=? WHERE id=?", (str(err)[:300], e["id"]))
    falhas = c.execute("UPDATE piloto_destinos SET falhas=falhas+1, erro=? WHERE id=? RETURNING falhas",
                       (str(err)[:300], e["destino_id"])).fetchone()[0]
    if falhas >= FALHAS_PARA_DESLIGAR:
        c.execute("UPDATE piloto_destinos SET ativo=0 WHERE id=?", (e["destino_id"],))
        c.commit()
        _avisar(uid, f"⚠️ ThyfIA — desliguei o destino “{e['d_nome'] or e['ref']}” depois de {falhas} falhas "
                     f"seguidas: {err}. Corrija e ligue de novo no painel.")
    c.commit()
    return {"acao": "erro", "tipo": err.tipo}


# --------------------------------------------------------------------------- aprovação (modo "aprovar")
def pendentes_aprovacao(uid: int) -> list[dict]:
    rows = db.conn().execute(
        "SELECT item_id, MIN(titulo) titulo, MIN(preco) preco, MIN(texto) texto, MIN(imagem) imagem, "
        "MIN(motivo) motivo, MIN(link) link, COUNT(*) destinos FROM piloto_envios "
        "WHERE usuario_id=? AND status='aguardando' GROUP BY item_id", (uid,)).fetchall()
    return [dict(r) for r in rows]


def aprovar(uid: int, item_id: int, agora: datetime | None = None) -> int:
    agora = agora or _agora()
    c = db.conn()
    ids = [r["id"] for r in c.execute("SELECT id FROM piloto_envios WHERE usuario_id=? AND item_id=? "
                                       "AND status='aguardando' ORDER BY id", (uid, item_id))]
    if not ids:
        raise ErroPiloto("Não há nada aguardando aprovação para esta oferta.", 404)
    quando = agora
    for i in ids:
        c.execute("UPDATE piloto_envios SET status='pendente', enviar_apos=?, criado_em=? WHERE id=?",
                  (_iso(quando), _iso(agora), i))
        quando += timedelta(seconds=random.randint(*ESPACO_DESTINOS_S))
    c.commit()
    return len(ids)


def pular(uid: int, item_id: int) -> int:
    c = db.conn()
    n = c.execute("UPDATE piloto_envios SET status='pulado', erro='Pulada pelo dono.' "
                  "WHERE usuario_id=? AND item_id=? AND status='aguardando'", (uid, item_id)).rowcount
    if not n:
        raise ErroPiloto("Não há nada aguardando aprovação para esta oferta.", 404)
    c.commit()
    return n


# --------------------------------------------------------------------------- painel e relatório
def _resumo_dia(uid: int, dia: str) -> dict:
    c = db.conn()
    um = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0  # noqa: E731
    return {
        "ofertas": um("SELECT COUNT(DISTINCT item_id) FROM piloto_envios WHERE usuario_id=? AND status='enviado' "
                      "AND substr(enviado_em,1,10)=?", uid, dia),
        "mensagens": um("SELECT COUNT(*) FROM piloto_envios WHERE usuario_id=? AND status='enviado' "
                        "AND substr(enviado_em,1,10)=?", uid, dia),
        "erros": um("SELECT COUNT(*) FROM piloto_envios WHERE usuario_id=? AND status='erro' "
                    "AND substr(criado_em,1,10)=?", uid, dia),
    }


def cobertura(uid: int, estoque: list[dict] | None = None) -> list[dict]:
    """Por nicho que tem grupo: quantos grupos e quantas ofertas prontas — mostra onde falta oferta."""
    estoque = candidatos(uid) if estoque is None else estoque
    por_nicho: dict[str, int] = {}
    for o in estoque:
        por_nicho[o["nicho"] or ""] = por_nicho.get(o["nicho"] or "", 0) + 1
    quant: dict[str, int] = {}
    for d in _destinos_prontos(uid):
        quant[d["nicho"] or nichos.GERAL] = quant.get(d["nicho"] or nichos.GERAL, 0) + 1
    saida = []
    for n in nichos.NICHOS:
        if n in quant:
            ofertas = len(estoque) if n == nichos.GERAL else por_nicho.get(n, 0)
            saida.append({"nicho": n, "nome": nichos.nome(n), "emoji": nichos.emoji(n), "grupos": quant[n],
                          "ofertas": ofertas})
    return saida


def status(uid: int) -> dict:
    agora = _agora()
    cfg = config(uid)
    c = db.conn()
    estoque = candidatos(uid, cfg, agora)
    recentes = [dict(r) for r in c.execute(
        "SELECT e.id, e.item_id, e.status, e.titulo, e.preco, e.busca, e.motivo, e.erro, e.enviado_em, e.criado_em, "
        "d.nome destino_nome, d.tipo destino_tipo, d.ref destino_ref FROM piloto_envios e "
        "LEFT JOIN piloto_destinos d ON d.id=e.destino_id WHERE e.usuario_id=? ORDER BY e.id DESC LIMIT 15", (uid,))]
    plataforma = {
        "qr": bool(configuracoes.obter("evolution_url") and configuracoes.obter("evolution_apikey")),
        "whatsapp": bool(configuracoes.obter("evolution_url") and configuracoes.obter("evolution_apikey")
                         and configuracoes.obter("evolution_instancia")),
        "telegram": bool(configuracoes.obter("telegram_bot_token")),
        "numero": configuracoes.obter("evolution_numero"),
        "bot": configuracoes.obter("telegram_bot"),
    }
    return {"config": cfg, "destinos": listar_destinos(uid), "plataforma": plataforma,
            "whatsapp": whatsapp_status(uid),
            "estoque": {"total": len(estoque), "baixo": len(estoque) < ESTOQUE_BAIXO,
                        "proximas": [{"titulo": o["titulo"], "preco": o["preco"], "busca": o["busca"],
                                      "pontos": o["pontos"], "motivos": o["motivos"], "nicho": o["nicho"]}
                                     for o in estoque[:5]]},
            "nichos": nichos.catalogo(), "cobertura": cobertura(uid, estoque),
            "hoje": _resumo_dia(uid, _iso(agora)[:10]), "aprovacao": pendentes_aprovacao(uid),
            "recentes": recentes, "agora": _iso(agora)}


def relatorio_texto(uid: int, agora: datetime | None = None) -> str:
    agora = agora or _agora()
    cfg = config(uid)
    dia = _resumo_dia(uid, _iso(agora)[:10])
    estoque = candidatos(uid, cfg, agora)
    top = db.conn().execute(
        "SELECT busca, COUNT(DISTINCT item_id) n FROM piloto_envios WHERE usuario_id=? AND status='enviado' "
        "AND substr(enviado_em,1,10)=? GROUP BY busca ORDER BY n DESC LIMIT 1", (uid, _iso(agora)[:10])).fetchone()
    linhas = ["📊 *ThyfIA — resumo de hoje*", "",
              f"✅ {dia['ofertas']} oferta(s) divulgada(s) ({dia['mensagens']} mensagens enviadas)"]
    if dia["erros"]:
        linhas.append(f"⚠️ {dia['erros']} envio(s) com erro — confira o painel")
    if top:
        linhas.append(f"🏆 Categoria que mais saiu: {top['busca']}")
    linhas.append(f"📦 Estoque: {len(estoque)} oferta(s) prontas para os próximos dias")
    cob = [x for x in cobertura(uid, estoque) if x["nicho"] != nichos.GERAL]
    if cob:
        linhas.append("🗂️ Por nicho: " + " · ".join(
            f"{x['emoji']} {x['nome']} {x['ofertas']}" + (" ⚠️" if x["ofertas"] < 2 else "") for x in cob))
    if len(estoque) < ESTOQUE_BAIXO:
        linhas.append("👉 Estoque baixo: gere novos links para o Piloto não ficar parado.")
    return "\n".join(linhas)


def enviar_relatorios(agora: datetime | None = None) -> int:
    agora = agora or _agora()
    if agora.hour < HORA_RELATORIO:
        return 0
    hoje = _iso(agora)[:10]
    c = db.conn()
    n = 0
    for r in c.execute("SELECT usuario_id FROM piloto_config WHERE ativo=1 AND relatorio=1 "
                       "AND COALESCE(ultimo_relatorio,'') <> ?", (hoje,)).fetchall():
        uid = r["usuario_id"]
        c.execute("UPDATE piloto_config SET ultimo_relatorio=? WHERE usuario_id=?", (hoje, uid))
        c.commit()
        _avisar(uid, relatorio_texto(uid, agora))
        n += 1
    return n


# --------------------------------------------------------------------------- agendador
def ciclo(agora: datetime | None = None) -> None:
    for r in db.conn().execute("SELECT usuario_id FROM piloto_config WHERE ativo=1").fetchall():
        uid = r["usuario_id"]
        try:
            planejar(uid, agora)
            despachar(uid, agora)
        except Exception:
            log.exception("Piloto: falha no ciclo do cliente %s (segue com os outros).", uid)
    try:
        enviar_relatorios(agora)
    except Exception:
        log.exception("Piloto: falha ao enviar relatórios.")


class PilotoAgendador(threading.Thread):
    def __init__(self, intervalo: float = INTERVALO_TICK):
        super().__init__(name="thyfia-piloto", daemon=True)
        self.intervalo = intervalo
        self.parar = threading.Event()

    def run(self) -> None:
        while not self.parar.is_set():
            try:
                ciclo()
            except Exception:
                log.exception("Piloto: falha no ciclo (segue no próximo).")
            self.parar.wait(self.intervalo)


_agendador: PilotoAgendador | None = None


def iniciar_agendador() -> PilotoAgendador | None:
    """THYFIA_PILOTO=0 desliga (usado nos testes)."""
    global _agendador
    if os.environ.get("THYFIA_PILOTO", "1").strip().lower() in ("0", "false", "off", "nao", "não"):
        return None
    if _agendador and _agendador.is_alive():
        return _agendador
    _agendador = PilotoAgendador()
    _agendador.start()
    return _agendador
