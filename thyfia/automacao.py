"""
Integração com a automação do n8n (publica as ofertas no WhatsApp/Telegram).

Configuração (variáveis de ambiente ou arquivo .env na raiz — NUNCA no código):
    N8N_WEBHOOK_URL   URL do webhook do n8n
    N8N_API_KEY       token enviado no header x-api-key

O token só existe no backend: não é logado, não vai para o front-end e não aparece
nas mensagens de erro.
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

from . import configuracoes

TIMEOUT_S = 30
TAMANHO_LOTE = 50


class ErroAutomacao(Exception):
    """Falha no envio. `codigo`: config | token | rede | n8n."""

    def __init__(self, mensagem: str, codigo: str):
        super().__init__(mensagem)
        self.codigo = codigo


# --------------------------------------------------------------------------- configuração
def config() -> tuple[str, str]:
    # configurado pelo admin na tela (Admin → Configurações); .env continua valendo como padrão
    url = configuracoes.obter("n8n_webhook_url")
    chave = configuracoes.obter("n8n_api_key")
    if not url or not chave:
        raise ErroAutomacao(
            "A automação ainda não foi habilitada na plataforma. Fale com o suporte.",
            "config",
        )
    if not url.startswith(("http://", "https://")):
        raise ErroAutomacao("N8N_WEBHOOK_URL inválida (deve começar com http).", "config")
    return url, chave


def configurada() -> bool:
    try:
        config()
        return True
    except ErroAutomacao:
        return False


# --------------------------------------------------------------------------- payload
def preco_numero(valor) -> float | None:
    """'1.234,56' / '27,06' / 'R$ 27' / 27.06  →  1234.56 / 27.06 / 27.0 / 27.06"""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s:
        return None
    if "," in s:  # formato brasileiro: ponto = milhar, vírgula = decimal
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _texto(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def montar_links(linhas: list[dict], keyword_padrao: str = "") -> list[dict]:
    """linhas: [{titulo, preco, url, link, busca?}] → itens do payload. Ignora quem não tem link.
    Cada item leva "keyword" = a busca que o gerou (ou keyword_padrao, se a linha não tiver)."""
    itens, vistos = [], set()
    for l in linhas:
        afiliado = _texto(l.get("link") or l.get("afiliado"))
        if not afiliado.lower().startswith("http") or afiliado in vistos:
            continue
        vistos.add(afiliado)
        item = {"titulo": _texto(l.get("titulo"))}
        preco = preco_numero(l.get("preco"))
        if preco is not None:  # preço que não deu para converter não é enviado
            item["preco"] = preco
        item["url"] = _texto(l.get("url"))
        item["afiliado"] = afiliado
        item["keyword"] = _texto(l.get("busca")) or _texto(keyword_padrao)
        itens.append(item)
    return itens


def lotes(itens: list, tamanho: int = TAMANHO_LOTE) -> list[list]:
    return [itens[i:i + tamanho] for i in range(0, len(itens), tamanho)]


def montar_payloads(keyword: str, linhas: list[dict], cliente: dict | None = None) -> list[dict]:
    kw = _texto(keyword)
    # "keyword" no topo continua sendo enviado (compatibilidade); cada link também leva o seu.
    # "cliente" diz ao n8n PARA QUEM divulgar (multi-tenant); só vai quando informado.
    ps = [{"keyword": kw, "links": lote} for lote in lotes(montar_links(linhas, kw))]
    if cliente:
        for p in ps:
            p["cliente"] = cliente
    return ps


def ler_planilha(caminho: str | Path) -> list[dict]:
    """Lê uma planilha no formato da ThyfIA (usado só na importação de gerações antigas;
    o envio sai do banco). Ver itens.ler_planilha."""
    from . import itens  # import local: itens depende deste módulo
    return itens.ler_planilha(caminho)


# --------------------------------------------------------------------------- envio
def _post(url: str, chave: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "x-api-key": chave,
                 "User-Agent": "ThyfIA/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            corpo = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ErroAutomacao("A plataforma não foi autorizada pela automação (401). Avise o suporte.", "token")
        raise ErroAutomacao(f"O n8n respondeu com erro {e.code}. Tente de novo em instantes.", "n8n")
    except (TimeoutError, socket.timeout):
        raise ErroAutomacao(f"O n8n não respondeu em {int(timeout)}s. Tente de novo.", "rede")
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise ErroAutomacao(f"O n8n não respondeu em {int(timeout)}s. Tente de novo.", "rede")
        raise ErroAutomacao("Não consegui falar com o n8n (falha de rede).", "rede")
    except OSError:
        raise ErroAutomacao("Não consegui falar com o n8n (falha de rede).", "rede")
    try:
        dados = json.loads(corpo) if corpo.strip() else {}
    except ValueError:
        raise ErroAutomacao("O n8n respondeu algo inesperado (não é JSON).", "n8n")
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        dados = dados[0]  # n8n às vezes devolve [ {...} ]
    return dados if isinstance(dados, dict) else {}


SEM_LINKS = "Geração sem links: não há links de afiliado salvos para enviar."


def agrupar_por_busca(linhas: list[dict], keyword_padrao: str) -> dict[str, list[dict]]:
    """Separa as linhas por palavra-chave (coluna "Busca"); mantém a ordem de aparição."""
    grupos: dict[str, list[dict]] = {}
    for l in linhas:
        kw = _texto(l.get("busca")) or _texto(keyword_padrao)
        grupos.setdefault(kw, []).append(l)
    return grupos


def enviar_itens(linhas: list[dict], keyword_padrao: str, timeout: float = TIMEOUT_S,
                 cliente: dict | None = None) -> dict:
    """Envia os itens de uma geração: uma série de chamadas (lotes de 50) por palavra-chave.
    Sem nenhum link válido → ErroAutomacao('vazio') e o n8n NÃO é chamado."""
    grupos = {kw: ls for kw, ls in agrupar_por_busca(linhas, keyword_padrao).items()
              if montar_links(ls)}
    if not grupos:
        raise ErroAutomacao(SEM_LINKS, "vazio")
    total = {"recebidos": 0, "novos": 0, "duplicados": 0, "invalidos": 0,
             "palavra_chave": "", "enviados": 0, "lotes": 0}
    palavras = []
    for kw, ls in grupos.items():
        r = enviar(kw, ls, timeout, cliente)
        for k in ("recebidos", "novos", "duplicados", "invalidos", "enviados", "lotes"):
            total[k] += r[k]
        palavras.append(r["palavra_chave"])
    total["palavra_chave"] = ", ".join(dict.fromkeys(palavras))
    return total


def enviar(keyword: str, linhas: list[dict], timeout: float = TIMEOUT_S, cliente: dict | None = None) -> dict:
    """Envia em lotes de até 50 e devolve o resumo somado:
    {recebidos, novos, duplicados, invalidos, palavra_chave, enviados, lotes}."""
    url, chave = config()
    payloads = montar_payloads(keyword, linhas, cliente)
    if not payloads:
        raise ErroAutomacao(SEM_LINKS, "vazio")
    resumo = {"recebidos": 0, "novos": 0, "duplicados": 0, "invalidos": 0,
              "palavra_chave": _texto(keyword), "enviados": 0, "lotes": 0}
    for p in payloads:
        r = _post(url, chave, p, timeout)
        for k in ("recebidos", "novos", "duplicados", "invalidos"):
            try:
                resumo[k] += int(r.get(k) or 0)
            except (TypeError, ValueError):
                pass
        if r.get("palavra_chave"):
            resumo["palavra_chave"] = str(r["palavra_chave"])
        resumo["enviados"] += len(p["links"])
        resumo["lotes"] += 1
    return resumo
