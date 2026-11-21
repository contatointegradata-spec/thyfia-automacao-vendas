"""
Agente de Publicação — escolhe o melhor item gerado e agenda o post no Instagram.

O agente SUGERE; a pessoa aprova (nada é publicado sem um clique).

  - candidatos: itens com título, preço, link de afiliado e imagem válidos, ainda não
    publicados/agendados (nem o mesmo produto vindo de outra geração);
  - ranking: `ranking_menor_preco` (isolado — troque `ESTRATEGIA` para mudar a regra);
  - agendamento: a API do Instagram não agenda feed, então uma thread do ThyfIA verifica a
    cada 60s os posts vencidos e publica (container → aguardar → publish). O ThyfIA precisa
    estar rodando no horário agendado.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime

from . import db, imagem_host, instagram
from .itens import TZ_SP, agora_iso, normalizar_cabecalho

log = logging.getLogger("thyfia.publicacao")

STATUS = ("rascunho", "agendado", "publicando", "publicado", "erro", "cancelado")
BLOQUEIAM = ("agendado", "publicando", "publicado")  # o item não volta a ser candidato
MAX_LEGENDA = 2200
MAX_HASHTAGS = 30
INTERVALO_AGENDADOR = 60
LIMITE_ATINGIDO = "Limite diário de publicações do Instagram atingido."
AVISO_AGENDADOR = "O ThyfIA precisa estar rodando no horário agendado para publicar."

_lock = threading.Lock()  # cria publicações uma de cada vez (evita agendar o mesmo item duas vezes)


class ErroPublicacao(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.status = status


# --------------------------------------------------------------------------- formatação
def preco_br(valor: float) -> str:
    """1234.5 → '1.234,50'"""
    return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ_SP)


def _agora() -> datetime:
    return datetime.now(TZ_SP)


# --------------------------------------------------------------------------- ranking (troque aqui)
def ranking_menor_preco(itens: list[dict], keyword: str = "") -> list[dict]:
    """Menor preço primeiro; empate → o mais recente. Devolve cópias com 'motivo'."""
    ordenados = sorted(
        itens,
        key=lambda i: (float(i["preco"]), -(_dt(i.get("criado_em")) or datetime.min.replace(tzinfo=TZ_SP)).timestamp(),
                       -int(i.get("id") or 0)))
    total = len(ordenados)
    onde = f"“{keyword}”" if keyword else "todas as buscas"
    saida = []
    for pos, it in enumerate(ordenados):
        preco = f"R$ {preco_br(it['preco'])}"
        if pos == 0:
            motivo = f"Menor preço entre {total} ite{'m' if total == 1 else 'ns'} de {onde}: {preco}."
        else:
            motivo = f"{pos + 1}º menor preço entre {total} itens de {onde}: {preco}."
        empate = [o for o in ordenados if o is not it and float(o["preco"]) == float(it["preco"])]
        if empate:
            motivo += " Empate de preço: o mais recente vem primeiro."
        saida.append({**it, "motivo": motivo, "posicao": pos + 1})
    return saida


ESTRATEGIA = ranking_menor_preco  # ex.: no futuro, ranking_maior_desconto


# --------------------------------------------------------------------------- legenda
GANCHOS = (
    "🔥 Achadinho do dia!",
    "😱 Olha esse preço!",
    "💸 Promoção que vale a pena!",
    "⚡ Corre antes que acabe!",
    "🛒 Achei baratinho pra você!",
)
HASHTAGS_FIXAS = ("#achadinhos", "#promocao", "#ofertas", "#mercadolivre")


def _hashtag(texto: str) -> str:
    t = re.sub(r"[^a-z0-9]", "", normalizar_cabecalho(texto))
    return f"#{t}" if t else ""


def gerar_legenda(item: dict, keyword: str = "") -> str:
    """Gancho, título, 'Por R$ X', chamada para a bio/canais e 5 hashtags.
    O link de afiliado NÃO entra (no Instagram ele não seria clicável)."""
    gancho = GANCHOS[int(item.get("id") or 0) % len(GANCHOS)]
    titulo = re.sub(r"\s+", " ", item.get("titulo") or "").strip()
    if len(titulo) > 180:
        titulo = titulo[:177].rstrip() + "…"
    tags = list(HASHTAGS_FIXAS)
    extra = _hashtag(keyword or item.get("busca") or "")
    tags.append(extra if extra and extra not in tags else "#achadinhosdodia")
    return (f"{gancho}\n\n{titulo}\n\n💰 Por R$ {preco_br(item['preco'])}\n\n"
            f"🔗 Link na bio e no nosso canal do Telegram/WhatsApp!\n\n{' '.join(tags)}")


# --------------------------------------------------------------------------- candidatos
_SQL_ELEGIVEIS = f"""
SELECT i.id, i.job_id, i.busca, i.titulo, i.preco, i.url_produto, i.link_afiliado, i.imagem, i.criado_em
  FROM job_itens i JOIN jobs j ON j.id = i.job_id
 WHERE j.usuario_id = ?
   AND TRIM(i.titulo) <> '' AND i.preco IS NOT NULL AND i.preco > 0 AND i.link_afiliado IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM publicacoes_instagram p JOIN job_itens i2 ON i2.id = p.item_id
         WHERE p.status IN ({','.join('?' * len(BLOQUEIAM))})
           AND (i2.id = i.id OR (i.url_produto <> '' AND i2.url_produto = i.url_produto)
                OR i2.link_afiliado = i.link_afiliado))
"""


def itens_elegiveis(uid: int, keyword: str | None = None, job_id: str | None = None) -> list[dict]:
    """Itens prontos para virar post (sem checar imagem — isso é feito no `sugerir`)."""
    sql, args = _SQL_ELEGIVEIS, [uid, *BLOQUEIAM]
    if job_id:
        sql += " AND i.job_id = ?"
        args.append(job_id)
    rows = [dict(r) for r in db.conn().execute(sql + " ORDER BY i.id DESC", args).fetchall()]
    if keyword:
        alvo = normalizar_cabecalho(keyword)
        rows = [r for r in rows if normalizar_cabecalho(r["busca"]) == alvo]
    # o mesmo produto em várias gerações conta uma vez só (fica o registro mais recente)
    vistos, unicos = set(), []
    for r in rows:
        chave = r["url_produto"] or r["link_afiliado"]
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(r)
    return unicos


def palavras_chave(uid: int) -> list[str]:
    """Buscas com itens elegíveis, da mais recente para a mais antiga."""
    vistas, saida = set(), []
    for r in itens_elegiveis(uid):
        n = normalizar_cabecalho(r["busca"])
        if n and n not in vistas:
            vistas.add(n)
            saida.append(r["busca"])
    return saida


def sugerir(uid: int, keyword: str | None = None, job_id: str | None = None, quantidade: int = 5,
            host: imagem_host.ImageHost | None = None) -> dict:
    """Top N candidatos com imagem válida, motivo do ranking e legenda sugerida."""
    quantidade = max(1, min(int(quantidade or 5), 20))
    keyword = (keyword or "").strip()
    if not keyword and not job_id:
        recentes = palavras_chave(uid)
        keyword = recentes[0] if recentes else ""
    elegiveis = itens_elegiveis(uid, keyword or None, job_id)
    ranqueados = ESTRATEGIA(elegiveis, keyword)
    host = host or imagem_host.obter()
    candidatos, sem_imagem = [], 0
    c = db.conn()
    for it in ranqueados:
        if len(candidatos) >= quantidade:
            break
        url = host.obter_url(it)
        if not url:  # continua candidato: a pessoa pode enviar a foto na tela
            sem_imagem += 1
        elif url != it.get("imagem"):
            c.execute("UPDATE job_itens SET imagem=? WHERE id=?", (url, it["id"]))
            c.commit()
        candidatos.append({
            "item_id": it["id"], "job_id": it["job_id"], "busca": it["busca"], "titulo": it["titulo"],
            "preco": it["preco"], "preco_fmt": f"R$ {preco_br(it['preco'])}",
            "url_produto": it["url_produto"], "link_afiliado": it["link_afiliado"],
            "criado_em": it["criado_em"], "image_url": url or "", "precisa_foto": not url,
            "motivo": it["motivo"],
            "posicao": it["posicao"], "legenda": gerar_legenda(it, keyword),
        })
    return {"keyword": keyword, "candidatos": candidatos, "total_elegiveis": len(elegiveis),
            "sem_imagem": sem_imagem, "palavras": palavras_chave(uid)}


# --------------------------------------------------------------------------- publicações
def _pub_dict(r) -> dict:
    d = dict(r)
    for k in ("titulo", "preco", "busca", "link_afiliado"):
        d.setdefault(k, None)
    return d


def listar(uid: int, limite: int = 100) -> list[dict]:
    rows = db.conn().execute(
        "SELECT p.id, p.item_id, p.status, p.legenda, p.image_url, p.agendado_para, p.publicado_em, "
        "p.ig_media_id, p.erro, p.criado_em, i.titulo, i.preco, i.busca, i.link_afiliado "
        "FROM publicacoes_instagram p LEFT JOIN job_itens i ON i.id = p.item_id "
        "WHERE p.usuario_id = ? ORDER BY COALESCE(p.agendado_para, p.criado_em) DESC, p.id DESC LIMIT ?",
        (uid, limite)).fetchall()
    return [_pub_dict(r) for r in rows]


def obter(pub_id: int) -> dict | None:
    r = db.conn().execute("SELECT * FROM publicacoes_instagram WHERE id=?", (pub_id,)).fetchone()
    return dict(r) if r else None


def _validar_entrada(uid: int, item_id: int | None, legenda: str, image_url: str) -> tuple[str, str]:
    if item_id is not None:
        _validar_item(uid, item_id)
    return _validar_post(legenda, image_url)


def _validar_item(uid: int, item_id: int) -> None:
    item = db.conn().execute(
        "SELECT i.* FROM job_itens i JOIN jobs j ON j.id=i.job_id WHERE i.id=? AND j.usuario_id=?",
        (item_id, uid)).fetchone()
    if not item:
        raise ErroPublicacao("Item não encontrado.", 404)
    if not item["link_afiliado"] or item["preco"] is None or not (item["titulo"] or "").strip():
        raise ErroPublicacao("Esse item não tem título, preço e link de afiliado válidos.")
    if _bloqueado(item):
        raise ErroPublicacao("Esse produto já foi publicado ou está agendado.", 409)


def _validar_post(legenda: str, image_url: str) -> tuple[str, str]:
    legenda = (legenda or "").strip()
    if not legenda:
        raise ErroPublicacao("A legenda não pode ficar vazia.")
    if len(legenda) > MAX_LEGENDA:
        raise ErroPublicacao(f"A legenda passou de {MAX_LEGENDA} caracteres ({len(legenda)}).")
    if len(re.findall(r"(?<!\w)#\w", legenda)) > MAX_HASHTAGS:
        raise ErroPublicacao(f"O Instagram aceita no máximo {MAX_HASHTAGS} hashtags.")
    image_url = imagem_host.para_jpg(image_url or "")
    if not image_url:
        raise ErroPublicacao("Escolha uma foto (envie uma imagem ou use a do produto).")
    if imagem_host.e_local(image_url):  # foto enviada pela tela: vira link público ao publicar
        if not imagem_host.arquivo_local(image_url, db.MIDIA):
            raise ErroPublicacao("A foto enviada não foi encontrada. Envie de novo.")
        return legenda, image_url
    if not imagem_host.url_publica(image_url):
        raise ErroPublicacao("A imagem precisa ser uma URL pública https (localhost não funciona no Instagram).")
    if not imagem_host.validar_jpeg(image_url):
        raise ErroPublicacao("A imagem não respondeu como JPEG (HTTP 200, image/jpeg). Use outra URL.")
    return legenda, image_url


def _bloqueado(item) -> bool:
    r = db.conn().execute(
        f"SELECT 1 FROM publicacoes_instagram p JOIN job_itens i2 ON i2.id=p.item_id "
        f"WHERE p.status IN ({','.join('?' * len(BLOQUEIAM))}) AND (i2.id=? OR "
        f"(?<>'' AND i2.url_produto=?) OR i2.link_afiliado=?) LIMIT 1",
        (*BLOQUEIAM, item["id"], item["url_produto"], item["url_produto"], item["link_afiliado"])).fetchone()
    return bool(r)


def _inserir(uid, item_id, legenda, image_url, status, agendado_para) -> int:
    c = db.conn()
    cur = c.execute(
        "INSERT INTO publicacoes_instagram (item_id, usuario_id, status, legenda, image_url, agendado_para, criado_em) "
        "VALUES (?,?,?,?,?,?,?)", (item_id, uid, status, legenda, image_url, agendado_para, agora_iso()))
    c.commit()
    return cur.lastrowid


def agendar(uid: int, item_id: int | None, legenda: str, image_url: str, agendado_para: str) -> dict:
    quando = _dt(agendado_para)
    if not quando:
        raise ErroPublicacao("Data/hora inválida.")
    if quando < _agora().replace(second=0, microsecond=0):
        raise ErroPublicacao("Escolha um horário no futuro.")
    with _lock:
        legenda, image_url = _validar_entrada(uid, item_id, legenda, image_url)
        pid = _inserir(uid, item_id, legenda, image_url, "agendado", quando.isoformat(timespec="seconds"))
    log.info("Publicação %s agendada para %s.", pid, quando.isoformat(timespec="minutes"))
    return obter(pid)


def publicar_agora(uid: int, item_id: int | None, legenda: str, image_url: str, confirmar: bool) -> dict:
    if confirmar is not True:
        raise ErroPublicacao("Confirme explicitamente para publicar agora.")
    if not instagram.configurado():
        raise ErroPublicacao(instagram.NAO_CONFIGURADO + ".", 503)
    with _lock:
        legenda, image_url = _validar_entrada(uid, item_id, legenda, image_url)
        pid = _inserir(uid, item_id, legenda, image_url, "publicando", agora_iso())
    _executar(pid, agendada=False)
    return obter(pid)


def editar(uid: int, pub_id: int, legenda: str, image_url: str, agendado_para: str) -> dict:
    """Altera legenda, foto e horário de uma publicação ainda AGENDADA."""
    p = obter(pub_id)
    if not p or p["usuario_id"] != uid:
        raise ErroPublicacao("Publicação não encontrada.", 404)
    if p["status"] != "agendado":
        raise ErroPublicacao(f"Só dá para alterar publicações agendadas (status atual: {p['status']}).", 409)
    quando = _dt(agendado_para)
    if not quando:
        raise ErroPublicacao("Data/hora inválida.")
    if quando < _agora().replace(second=0, microsecond=0):
        raise ErroPublicacao("Escolha um horário no futuro.")
    legenda, image_url = _validar_post(legenda, image_url)
    c = db.conn()
    cur = c.execute(  # WHERE status='agendado': se o agendador já pegou, não altera
        "UPDATE publicacoes_instagram SET legenda=?, image_url=?, agendado_para=?, erro=NULL "
        "WHERE id=? AND usuario_id=? AND status='agendado'",
        (legenda, image_url, quando.isoformat(timespec="seconds"), pub_id, uid))
    c.commit()
    if not cur.rowcount:
        raise ErroPublicacao("Essa publicação acabou de sair da fila (já está sendo publicada).", 409)
    log.info("Publicação %s alterada (agora para %s).", pub_id, quando.isoformat(timespec="minutes"))
    return obter(pub_id)


def cancelar(uid: int, pub_id: int) -> dict:
    c = db.conn()
    cur = c.execute("UPDATE publicacoes_instagram SET status='cancelado' "
                    "WHERE id=? AND usuario_id=? AND status IN ('agendado','rascunho')", (pub_id, uid))
    c.commit()
    if not cur.rowcount:
        p = obter(pub_id)
        if not p or p["usuario_id"] != uid:
            raise ErroPublicacao("Publicação não encontrada.", 404)
        raise ErroPublicacao(f"Só dá para cancelar publicações agendadas (status atual: {p['status']}).", 409)
    return obter(pub_id)


def _atualizar(pub_id: int, **campos) -> None:
    c = db.conn()
    c.execute(f"UPDATE publicacoes_instagram SET {', '.join(f'{k}=?' for k in campos)} WHERE id=?",
              (*campos.values(), pub_id))
    c.commit()


def _executar(pub_id: int, agendada: bool) -> None:
    """Publica uma publicação que JÁ está em 'publicando' (o status funciona como trava)."""
    p = obter(pub_id)
    try:
        if instagram.limite_publicacao()["restante"] <= 0:
            if agendada:  # volta para a fila e tenta de novo no próximo ciclo
                _atualizar(pub_id, status="agendado", erro=LIMITE_ATINGIDO + " Tentando de novo em breve.")
                log.warning("Publicação %s adiada: %s", pub_id, LIMITE_ATINGIDO)
                return
            raise instagram.ErroInstagram(LIMITE_ATINGIDO + " Agende para mais tarde.", "limite")
        url = imagem_host.url_para_instagram(p["image_url"], db.MIDIA)
        media_id = instagram.publicar_imagem(url, p["legenda"])
    except (instagram.ErroInstagram, imagem_host.ErroImagem) as e:
        _atualizar(pub_id, status="erro", erro=str(e))
        log.warning("Publicação %s falhou: %s", pub_id, e)
        return
    except Exception as e:  # nunca derruba o agendador
        _atualizar(pub_id, status="erro", erro=f"Erro inesperado: {instagram.limpar(type(e).__name__)}")
        log.warning("Publicação %s falhou com erro inesperado (%s).", pub_id, type(e).__name__)
        return
    _atualizar(pub_id, status="publicado", publicado_em=agora_iso(), ig_media_id=media_id, erro=None)
    log.info("Publicação %s publicada no Instagram (mídia %s).", pub_id, media_id)


# --------------------------------------------------------------------------- agendador
def processar_vencidos(agora: datetime | None = None) -> int:
    """Publica os agendados vencidos. Cada um é 'travado' com UPDATE ... WHERE status='agendado',
    então nunca sai duas vezes (nem com dois processos/threads). Retorna quantos tentou."""
    agora = agora or _agora()
    c = db.conn()
    feitos = 0
    for r in c.execute("SELECT id, agendado_para FROM publicacoes_instagram WHERE status='agendado' "
                       "ORDER BY agendado_para, id").fetchall():
        quando = _dt(r["agendado_para"])
        if not quando or quando > agora:
            continue
        cur = c.execute("UPDATE publicacoes_instagram SET status='publicando', erro=NULL "
                        "WHERE id=? AND status='agendado'", (r["id"],))
        c.commit()
        if cur.rowcount != 1:
            continue  # outro processo pegou
        feitos += 1
        _executar(r["id"], agendada=True)
    return feitos


class Agendador(threading.Thread):
    def __init__(self, intervalo: float = INTERVALO_AGENDADOR):
        super().__init__(name="thyfia-agendador-instagram", daemon=True)
        self.intervalo = intervalo
        self.parar = threading.Event()

    def run(self) -> None:
        while not self.parar.is_set():
            try:
                processar_vencidos()
            except Exception:
                log.exception("Agendador do Instagram: falha no ciclo (segue no próximo).")
            self.parar.wait(self.intervalo)


_agendador: Agendador | None = None


def agendador_ativo() -> bool:
    return bool(_agendador and _agendador.is_alive())


def iniciar_agendador() -> Agendador | None:
    """Sobe a thread (uma só). Pendentes de antes do reinício são retomados no 1º ciclo.
    THYFIA_AGENDADOR=0 desliga (usado nos testes)."""
    global _agendador
    if os.environ.get("THYFIA_AGENDADOR", "1").strip().lower() in ("0", "false", "off", "nao", "não"):
        return None
    if agendador_ativo():
        return _agendador
    pend = db.conn().execute("SELECT COUNT(*) FROM publicacoes_instagram WHERE status='agendado'").fetchone()[0]
    if pend:
        log.info("Agendador do Instagram: retomando %d publicação(ões) agendada(s).", pend)
    _agendador = Agendador()
    _agendador.start()
    return _agendador
