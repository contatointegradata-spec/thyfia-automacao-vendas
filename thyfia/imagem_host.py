"""
De onde vem a imagem do post (o Instagram exige uma URL PÚBLICA de um JPEG; localhost não serve).

ImageHost é o ponto de extensão:
  - "ml_direct" (atual): usa a foto do produto no CDN do Mercado Livre.
  - "proprio" (futuro): gerar um cartão 1080x1350 com Pillow e subir para a VPS.

Escolha por IG_IMAGE_HOST no .env (padrão ml_direct).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import requests

log = logging.getLogger("thyfia.imagem")

TIMEOUT = 15
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
RE_OG = (re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I),
         re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', re.I))


class ErroImagem(Exception):
    pass


# --------------------------------------------------------------------------- utilitários
def para_jpg(url: str) -> str:
    """Troca .webp por .jpg (o CDN do ML serve o mesmo arquivo em JPEG)."""
    return re.sub(r"\.webp(?=$|[?#])", ".jpg", (url or "").strip(), flags=re.I)


def url_publica(url: str) -> bool:
    """https e host público (localhost / IP privado não funcionam no Instagram)."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return True  # nome de domínio


def validar_jpeg(url: str) -> bool:
    """A URL responde 200 e é image/jpeg? Usa HEAD; se o servidor não aceitar HEAD
    (o CDN do ML responde 405), faz um GET lendo só os primeiros bytes."""
    if not url_publica(url):
        return False
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True, headers={"User-Agent": UA})
        if r.status_code == 200 and r.headers.get("Content-Type", "").lower().startswith("image/jpeg"):
            return True
        if r.status_code not in (403, 405, 501):
            return False
        g = requests.get(url, timeout=TIMEOUT, stream=True, headers={"User-Agent": UA})
        try:
            tipo = g.headers.get("Content-Type", "").lower()
            inicio = next(g.iter_content(3), b"") if g.status_code == 200 else b""
        finally:
            g.close()
        return tipo.startswith("image/jpeg") and inicio[:3] == b"\xff\xd8\xff"
    except requests.RequestException:
        return False


def og_image(url_produto: str) -> str | None:
    """og:image da página do produto (o ML costuma bloquear requisições soltas — aí volta None)."""
    if not (url_produto or "").startswith("http"):
        return None
    try:
        r = requests.get(url_produto, timeout=TIMEOUT, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    except requests.RequestException:
        return None
    if r.status_code != 200 or "verification" in r.url or "captcha" in r.url:
        return None
    for rx in RE_OG:
        m = rx.search(r.text)
        if m:
            return m.group(1).replace("&amp;", "&")
    return None


# --------------------------------------------------------------------------- hosts
class ImageHost(ABC):
    nome = ""

    @abstractmethod
    def obter_url(self, item: dict) -> str | None:
        """URL pública (JPEG validado) para o item, ou None se não houver imagem utilizável."""


class MlDirect(ImageHost):
    """Foto do produto direto do CDN do Mercado Livre."""
    nome = "ml_direct"

    def obter_url(self, item: dict) -> str | None:
        tentativas = [item.get("imagem")]
        if not item.get("imagem"):
            tentativas.append(og_image(item.get("url_produto") or ""))
        for bruta in tentativas:
            if bruta:
                url = para_jpg(bruta)
                if validar_jpeg(url):
                    return url
        return None


class HostProprio(ImageHost):
    """FUTURO: gerar um cartão 1080x1350 com Pillow (foto + título + preço) e subir para a
    VPS, devolvendo a URL pública. Implementar `gerar_cartao` e `enviar` e ativar com
    IG_IMAGE_HOST=proprio."""
    nome = "proprio"

    def gerar_cartao(self, item: dict) -> bytes:  # pragma: no cover - ponto de extensão
        raise NotImplementedError

    def enviar(self, jpeg: bytes, nome: str) -> str:  # pragma: no cover - ponto de extensão
        raise NotImplementedError

    def obter_url(self, item: dict) -> str | None:  # pragma: no cover - ponto de extensão
        return self.enviar(self.gerar_cartao(item), f"item-{item.get('id')}.jpg")


# --------------------------------------------------------------------------- foto enviada pela pessoa
MAX_UPLOAD = 8 * 1024 * 1024
PROPORCAO_MIN, PROPORCAO_MAX = 4 / 5, 1.91  # largura/altura aceitas pelo feed
# Foto enviada pela tela: o Instagram não alcança o localhost. Na HORA de publicar, a foto vai
# para um link público temporário (expira sozinho; o Instagram copia a imagem ao criar o post).
# Se THYFIA_URL_PUBLICA estiver definido (domínio/túnel deste ThyfIA), ele é usado no lugar.
HOST_TEMP_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
TEMPO_TEMP = "1h"
AVISO_TEMP = "Foto pronta. Na hora de publicar, ela vai para um link temporário (1h) para o Instagram buscar."


def url_base_publica() -> str:
    return (os.environ.get("THYFIA_URL_PUBLICA") or "").strip().rstrip("/")


def preparar_jpeg(dados: bytes) -> bytes:
    """Qualquer imagem (PNG, WEBP, JPEG, HEIC não) → JPEG até 1440px, com margem branca se a
    proporção estiver fora do que o feed aceita."""
    import io

    from PIL import Image, ImageOps, UnidentifiedImageError

    if not dados:
        raise ErroImagem("Arquivo vazio.")
    if len(dados) > MAX_UPLOAD:
        raise ErroImagem("A foto passa de 8 MB.")
    try:
        img = Image.open(io.BytesIO(dados))
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ErroImagem("Não reconheci o arquivo como imagem (use JPG, PNG ou WEBP).") from None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fundo = Image.new("RGB", img.size, "white")
        fundo.paste(img, mask=img.split()[-1])
        img = fundo
    else:
        img = img.convert("RGB")
    w, h = img.size
    prop = w / h
    if prop < PROPORCAO_MIN:      # alta demais → margem nas laterais
        novo = (round(h * PROPORCAO_MIN), h)
    elif prop > PROPORCAO_MAX:    # larga demais → margem em cima/embaixo
        novo = (w, round(w / PROPORCAO_MAX))
    else:
        novo = None
    if novo:
        tela = Image.new("RGB", novo, "white")
        tela.paste(img, ((novo[0] - w) // 2, (novo[1] - h) // 2))
        img = tela
    img.thumbnail((1440, 1800))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def salvar_upload(dados: bytes, pasta) -> dict:
    """Salva a foto como JPEG em dados/midia e devolve {arquivo, local_url, image_url, publica, aviso}."""
    import secrets
    from pathlib import Path

    jpeg = preparar_jpeg(dados)
    nome = f"{secrets.token_urlsafe(12)}.jpg"
    Path(pasta).mkdir(parents=True, exist_ok=True)
    (Path(pasta) / nome).write_bytes(jpeg)
    base = url_base_publica()
    publica = bool(base) and url_publica(base + "/x")
    local = f"/midia/{nome}"
    return {"arquivo": nome, "local_url": local,
            "image_url": f"{base}{local}" if publica else local,  # local → link temporário ao publicar
            "publica": publica, "aviso": "Foto pronta." if publica else AVISO_TEMP}


def e_local(url: str) -> bool:
    return (url or "").startswith("/midia/")


def arquivo_local(url: str, pasta):
    """'/midia/abc.jpg' → caminho em dados/midia (sem permitir sair da pasta)."""
    from pathlib import Path

    nome = Path(url).name
    if not e_local(url) or not re.fullmatch(r"[\w-]+\.jpg", nome):
        return None
    arq = Path(pasta) / nome
    return arq if arq.is_file() else None


def subir_temporario(arq) -> str:
    """Envia o JPEG para o link público temporário e devolve a URL validada."""
    from pathlib import Path

    try:
        with open(arq, "rb") as f:
            r = requests.post(HOST_TEMP_URL, data={"reqtype": "fileupload", "time": TEMPO_TEMP},
                              files={"fileToUpload": (Path(arq).name, f, "image/jpeg")},
                              headers={"User-Agent": "ThyfIA/1.0"}, timeout=90)
    except (requests.RequestException, OSError) as e:
        raise ErroImagem(f"Não consegui gerar o link público da foto ({type(e).__name__}). Tente de novo.") from None
    url = (r.text or "").strip()
    if r.status_code != 200 or not url.startswith("https://") or len(url) > 300:
        raise ErroImagem(f"O serviço de link temporário da foto falhou (HTTP {r.status_code}). Tente de novo.")
    if not validar_jpeg(url):
        raise ErroImagem("O link temporário da foto não respondeu como JPEG. Tente de novo.")
    return url


def url_para_instagram(image_url: str, pasta) -> str:
    """URL que o Instagram consegue buscar. Fotos locais viram link público na hora."""
    if not e_local(image_url):
        return image_url
    arq = arquivo_local(image_url, pasta)
    if not arq:
        raise ErroImagem("A foto enviada não foi encontrada no ThyfIA. Envie de novo.")
    base = url_base_publica()
    if base and url_publica(base + "/x"):
        return f"{base}/midia/{arq.name}"
    return subir_temporario(arq)


HOSTS = {MlDirect.nome: MlDirect, HostProprio.nome: HostProprio}


def obter(nome: str | None = None) -> ImageHost:
    nome = (nome or os.environ.get("IG_IMAGE_HOST") or MlDirect.nome).strip().lower()
    if nome not in HOSTS:
        log.warning("IG_IMAGE_HOST=%s desconhecido — usando ml_direct.", nome)
        nome = MlDirect.nome
    return HOSTS[nome]()
