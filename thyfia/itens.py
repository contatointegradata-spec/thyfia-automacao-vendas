"""
Itens de cada geração (tabela job_itens) — a fonte dos dados da ThyfIA.

O envio ao n8n e o download da planilha saem daqui. A planilha local virou só um arquivo
opcional (cópia), e o Google Sheets é alimentado apenas pelo n8n via webhook.

Formato da planilha (importação de gerações antigas e download), nesta ordem:
    Data/Hora | Busca | Título | Preço (R$) | URL do Produto | Link de Afiliado
"""

from __future__ import annotations

import io
import logging
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import db
from .automacao import preco_numero

log = logging.getLogger("thyfia.itens")

CABECALHOS = ["Data/Hora", "Busca", "Título", "Preço (R$)", "URL do Produto", "Link de Afiliado"]
FORMATO_DATA = "%d/%m/%Y %H:%M"  # padrão usado na planilha
NAO_GERADO = "NÃO GERADO"

try:
    from zoneinfo import ZoneInfo
    TZ_SP = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem base de fusos: São Paulo é UTC-3 (sem horário de verão desde 2019)
    TZ_SP = timezone(timedelta(hours=-3), "America/Sao_Paulo")


# --------------------------------------------------------------------------- normalização
def normalizar_cabecalho(texto) -> str:
    """'  Título ' / 'TITULO' / 'Preço  (R$)' → 'titulo' / 'titulo' / 'preco (r$)'"""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip().lower()


_CHAVES = {normalizar_cabecalho(c): k for c, k in zip(
    CABECALHOS, ("data", "busca", "titulo", "preco", "url", "link"))}


def _texto(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _numero(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _inteiro(v) -> int | None:
    n = _numero(v)
    return int(n) if n is not None else None


def agora_iso() -> str:
    return datetime.now(TZ_SP).isoformat(timespec="seconds")


def data_iso(valor) -> str | None:
    """Data/Hora da planilha → ISO com fuso. 'dd/mm/aaaa hh:mm' é horário de São Paulo."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        dt = valor if valor.tzinfo else valor.replace(tzinfo=TZ_SP)
        return dt.isoformat(timespec="seconds")
    s = _texto(valor)
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ_SP).isoformat(timespec="seconds")
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s)
        return (dt if dt.tzinfo else dt.replace(tzinfo=TZ_SP)).isoformat(timespec="seconds")
    except ValueError:
        return None


def data_planilha(iso: str | None) -> str:
    """ISO do banco → 'dd/mm/aaaa hh:mm' no horário de São Paulo."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo:
        dt = dt.astimezone(TZ_SP)
    return dt.strftime(FORMATO_DATA)


def _link_valido(link) -> str | None:
    s = _texto(link)
    return s if s.lower().startswith("http") else None


# --------------------------------------------------------------------------- gravação / leitura
def salvar_item(job_id: str, busca: str, titulo, preco, url_produto, link_afiliado,
                criado_em: str | None = None, conn: sqlite3.Connection | None = None,
                commit: bool = True, imagem: str | None = None, processado: int = 1,
                selo: str | None = None, preco_de=None, desconto_pct=None, condicao: str | None = None,
                vendas=None, nota=None) -> bool:
    """Grava um item da geração. Ignora link de afiliado repetido no mesmo job.
    Retorna True se gravou. `selo`, `preco_de`, `desconto_pct`, `condicao`, `vendas` e `nota` vêm do cartão da busca."""
    c = conn or db.conn()
    cur = c.execute(
        "INSERT OR IGNORE INTO job_itens (job_id, busca, titulo, preco, url_produto, link_afiliado, "
        "criado_em, imagem, processado, selo, preco_de, desconto_pct, condicao, vendas, nota) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, _texto(busca), _texto(titulo), preco_numero(preco), _texto(url_produto),
         _link_valido(link_afiliado), criado_em or agora_iso(), _link_valido(imagem), processado,
         _texto(selo)[:40] or None, _numero(preco_de), _inteiro(desconto_pct),
         condicao if condicao in ("cupom", "pix") else None, _inteiro(vendas), _numero(nota)),
    )
    if commit:
        c.commit()
    return cur.rowcount > 0


def itens_do_job(job_id: str) -> list[dict]:
    rows = db.conn().execute(
        "SELECT busca, titulo, preco, url_produto, link_afiliado, criado_em FROM job_itens "
        "WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def contar(job_id: str) -> tuple[int, int]:
    """(total de itens, itens com link de afiliado)"""
    r = db.conn().execute(
        "SELECT COUNT(*) AS t, COUNT(link_afiliado) AS l FROM job_itens WHERE job_id=?", (job_id,)
    ).fetchone()
    return r["t"], r["l"]


def pendentes(job_id: str) -> list[str]:
    """URLs dos produtos que ainda aguardam o link (geração feita pelo navegador da pessoa)."""
    rows = db.conn().execute(
        "SELECT url_produto FROM job_itens WHERE job_id=? AND processado=0 ORDER BY id", (job_id,)).fetchall()
    return [r["url_produto"] for r in rows]


def processados(job_id: str) -> int:
    return db.conn().execute(
        "SELECT COUNT(*) AS n FROM job_itens WHERE job_id=? AND processado=1", (job_id,)).fetchone()["n"]


def registrar_resultado(job_id: str, url_produto: str, link: str | None) -> bool:
    """Grava o link que o navegador da pessoa gerou (ou a falta dele) para um produto pendente.
    Retorna False se o produto não estava pendente (resposta repetida/desconhecida)."""
    c = db.conn()
    try:
        cur = c.execute(
            "UPDATE job_itens SET link_afiliado=?, processado=1 WHERE job_id=? AND url_produto=? AND processado=0",
            (_link_valido(link), job_id, _texto(url_produto)))
    except sqlite3.IntegrityError:  # o mesmo link já está em outro item deste job
        cur = c.execute(
            "UPDATE job_itens SET processado=1 WHERE job_id=? AND url_produto=? AND processado=0",
            (job_id, _texto(url_produto)))
    c.commit()
    return cur.rowcount > 0


def para_envio(itens: list[dict]) -> list[dict]:
    """Itens do banco → linhas no formato que automacao.montar_links espera."""
    return [{"busca": i["busca"], "titulo": i["titulo"], "preco": i["preco"],
             "url": i["url_produto"], "link": i["link_afiliado"]} for i in itens]


# --------------------------------------------------------------------------- planilha
def ler_planilha(caminho: str | Path) -> list[dict]:
    """Lê uma planilha no formato da ThyfIA, localizando as colunas pelo cabeçalho (ignora
    maiúsculas, acentos e espaços). Levanta ValueError se faltar 'Link de Afiliado'."""
    wb = load_workbook(caminho, read_only=True, data_only=True)
    try:
        linhas = list(wb.active.iter_rows(values_only=True))
    finally:
        wb.close()
    if not linhas:
        raise ValueError("planilha vazia")
    pos = {}
    for i, cab in enumerate(linhas[0]):
        chave = _CHAVES.get(normalizar_cabecalho(cab))
        if chave and chave not in pos:
            pos[chave] = i
    if "link" not in pos:
        raise ValueError("coluna 'Link de Afiliado' não encontrada")
    pega = lambda row, k: row[pos[k]] if k in pos and pos[k] < len(row) else None
    return [
        {"criado_em": pega(r, "data"), "busca": pega(r, "busca"), "titulo": pega(r, "titulo"),
         "preco": pega(r, "preco"), "url": pega(r, "url"), "link": pega(r, "link")}
        for r in linhas[1:] if r and any(v not in (None, "") for v in r)
    ]


def importar_planilha(job_id: str, termo: str, caminho: str | Path) -> int:
    """Grava no banco os itens de uma planilha antiga. Retorna quantos itens gravou."""
    c = db.conn()
    gravados = 0
    for l in ler_planilha(caminho):
        gravados += salvar_item(
            job_id, _texto(l["busca"]) or termo, l["titulo"], l["preco"], l["url"], l["link"],
            criado_em=data_iso(l["criado_em"]) or agora_iso(), conn=c, commit=False)
    c.execute("UPDATE jobs SET itens_importados=1 WHERE id=?", (job_id,))
    c.commit()
    return gravados


def importar_antigas() -> dict:
    """Na inicialização: para cada job com planilha local e sem itens no banco, lê a planilha
    uma vez. Arquivo faltando ou inválido → aviso no log e segue (nunca derruba o servidor)."""
    res = {"importados": 0, "itens": 0, "pulados": 0}
    rows = db.conn().execute(
        "SELECT j.id, j.termo, j.arquivo FROM jobs j WHERE j.arquivo IS NOT NULL "
        "AND j.itens_importados=0 AND NOT EXISTS (SELECT 1 FROM job_itens i WHERE i.job_id=j.id)"
    ).fetchall()
    for r in rows:
        arq = Path(r["arquivo"])
        if not arq.exists():
            log.warning("Importação: planilha do job %s não encontrada (%s) — pulando.", r["id"], arq)
            res["pulados"] += 1
            continue
        try:
            n = importar_planilha(r["id"], r["termo"], arq)
        except Exception as e:  # arquivo corrompido, sem coluna de link etc.
            db.conn().rollback()
            log.warning("Importação: planilha do job %s ignorada (%s): %s", r["id"], arq.name, e)
            res["pulados"] += 1
            continue
        res["importados"] += 1
        res["itens"] += n
        log.info("Importação: job %s → %d itens lidos da planilha antiga.", r["id"], n)
    return res


def gerar_planilha(job_id: str, termo: str = "") -> bytes:
    """Monta a planilha da geração a partir do banco (mesmo formato de sempre)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Links ThyfIA"
    ws.append(CABECALHOS)
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="5B3DF5")
        cel.alignment = Alignment(vertical="center")
    for col, larg in zip("ABCDEF", (17, 18, 60, 12, 70, 32)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A2"
    for i in itens_do_job(job_id):
        ws.append([data_planilha(i["criado_em"]), i["busca"] or termo, i["titulo"],
                   i["preco"], i["url_produto"], i["link_afiliado"] or NAO_GERADO])
        if i["preco"] is not None:
            ws.cell(row=ws.max_row, column=4).number_format = "#,##0.00"
        if i["link_afiliado"]:
            cel = ws.cell(row=ws.max_row, column=6)
            cel.hyperlink = i["link_afiliado"]
            cel.font = Font(color="2563EB", underline="single")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- limpeza
def limpar_planilhas(dias: int | None = None) -> int:
    """Apaga planilhas locais com mais de N dias (THYFIA_LIMPAR_PLANILHAS_DIAS; 0 = desligado).
    Só apaga a de jobs cujos itens já estão no banco — os dados nunca se perdem."""
    if dias is None:
        try:
            dias = int(os.environ.get("THYFIA_LIMPAR_PLANILHAS_DIAS") or 0)
        except ValueError:
            dias = 0
    if dias <= 0:
        return 0
    limite = time.time() - dias * 86400
    c = db.conn()
    apagados = 0
    for r in c.execute("SELECT id, arquivo FROM jobs WHERE arquivo IS NOT NULL").fetchall():
        arq = Path(r["arquivo"])
        try:
            if not arq.exists() or arq.stat().st_mtime > limite or contar(r["id"])[0] == 0:
                continue
            arq.unlink()
            apagados += 1
        except OSError as e:
            log.warning("Limpeza: não consegui apagar %s: %s", arq, e)
    if apagados:
        log.info("Limpeza: %d planilha(s) local(is) com mais de %d dias apagada(s).", apagados, dias)
    return apagados


def salvar_planilha_local() -> bool:
    """A cópia local em disco é opcional (THYFIA_SALVAR_PLANILHA=0 desliga)."""
    return os.environ.get("THYFIA_SALVAR_PLANILHA", "1").strip().lower() not in ("0", "false", "nao", "não", "off")
