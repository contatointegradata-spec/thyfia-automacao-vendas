"""
Vídeo para Reels: enviado pela pessoa (convertido para o formato do Instagram) ou gerado das fotos
dos produtos. Tudo com ffmpeg; o resultado fica em dados/midia (/midia/<nome>.mp4 + capa .jpg).

Formato de saída: MP4, H.264, 1080x1920 (9:16), 30 fps, AAC se houver áudio, "faststart".
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import imagem_host

log = logging.getLogger("thyfia.video")

MAX_UPLOAD = 100 * 1024 * 1024
DURACAO_MIN, DURACAO_MAX = 3.0, 90.0
SEGUNDOS_POR_FOTO = 3
MAX_FOTOS = 10
L, A, FPS = 1080, 1920, 30


class ErroVideo(Exception):
    pass


def disponivel() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _exigir_ffmpeg() -> None:
    if not disponivel():
        raise ErroVideo("O servidor ainda não tem o ffmpeg instalado (necessário para vídeos). Avise o suporte.")


def _rodar(cmd: list[str], timeout: int = 300) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ErroVideo("O vídeo demorou demais para processar. Tente um arquivo menor.") from None
    if r.returncode != 0:
        ultima = next((ln for ln in reversed((r.stderr or "").strip().splitlines()) if ln.strip()), "erro desconhecido")
        log.warning("ffmpeg falhou: %s", ultima[:300])
        raise ErroVideo("Não consegui processar o vídeo (arquivo corrompido ou formato incomum).")
    return r.stdout


def sondar(arq) -> dict:
    """→ {duracao, tem_video, tem_audio}"""
    saida = _rodar(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                    "-of", "json", str(arq)], timeout=60)
    try:
        d = json.loads(saida or "{}")
    except ValueError:
        d = {}
    tipos = {s.get("codec_type") for s in d.get("streams", [])}
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"duracao": dur, "tem_video": "video" in tipos, "tem_audio": "audio" in tipos}


def _novo_nome() -> str:
    return secrets.token_urlsafe(12).replace("_", "a").replace("-", "b")


def _capa(video: Path, destino: Path, segundo: float = 0.3) -> None:
    _rodar(["ffmpeg", "-y", "-v", "error", "-ss", str(segundo), "-i", str(video), "-frames:v", "1",
            "-q:v", "3", str(destino)], timeout=60)
    if not destino.is_file():  # vídeo mais curto que o ponto da capa
        _rodar(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(destino)],
               timeout=60)


# filtro: 9:16 com a imagem inteira no centro e o fundo borrado (nada é cortado)
_FILTRO_9x16 = (f"[0:v]split[a][b];[a]scale={L}:{A}:force_original_aspect_ratio=increase,crop={L}:{A},"
                f"boxblur=30:5[bg];[b]scale={L}:{A}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]")


def salvar_upload(dados: bytes, pasta) -> dict:
    """Vídeo enviado → MP4 9:16 em dados/midia. → {video_url, capa_url, duracao}"""
    _exigir_ffmpeg()
    if not dados:
        raise ErroVideo("Arquivo vazio.")
    if len(dados) > MAX_UPLOAD:
        raise ErroVideo("O vídeo passa de 100 MB. Use um arquivo menor.")
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=pasta) as tmp:
        entrada = Path(tmp) / "entrada"
        entrada.write_bytes(dados)
        try:
            info = sondar(entrada)
        except ErroVideo:
            raise ErroVideo("Não reconheci o arquivo como vídeo (use MP4 ou MOV).") from None
        if not info["tem_video"]:
            raise ErroVideo("Não reconheci o arquivo como vídeo (use MP4 ou MOV).")
        if info["duracao"] < DURACAO_MIN:
            raise ErroVideo(f"O vídeo precisa ter pelo menos {int(DURACAO_MIN)} segundos.")
        if info["duracao"] > DURACAO_MAX:
            raise ErroVideo(f"O vídeo passa de {int(DURACAO_MAX)} segundos. Corte-o antes de enviar.")
        nome = _novo_nome()
        saida = Path(tmp) / f"{nome}.mp4"
        audio = ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-map", "0:a:0"] if info["tem_audio"] else ["-an"]
        _rodar(["ffmpeg", "-y", "-v", "error", "-i", str(entrada), "-filter_complex", _FILTRO_9x16,
                "-map", "[v]", *audio, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-movflags", "+faststart", "-t", str(DURACAO_MAX), str(saida)])
        capa = Path(tmp) / f"{nome}.jpg"
        _capa(saida, capa)
        shutil.move(str(saida), pasta / f"{nome}.mp4")
        shutil.move(str(capa), pasta / f"{nome}.jpg")
    return {"video_url": f"/midia/{nome}.mp4", "capa_url": f"/midia/{nome}.jpg", "duracao": round(info["duracao"], 1)}


# --------------------------------------------------------------------------- vídeo a partir de fotos
def _fonte(tamanho: int):
    from PIL import ImageFont

    for caminho in ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(size=tamanho)


def _quebrar(draw, texto: str, fonte, largura: int, max_linhas: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = (atual + " " + palavra).strip()
        if draw.textlength(teste, font=fonte) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
        if len(linhas) == max_linhas:
            break
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    if len(linhas) == max_linhas and " ".join(linhas) != texto.strip():
        linhas[-1] = linhas[-1].rstrip(" .,") + "…"
    return linhas


def quadro(dados: bytes, titulo: str | None = None, preco: str | None = None) -> bytes:
    """Foto → quadro 1080x1920 (fundo desfocado) com a faixa de título e preço embaixo, se houver."""
    from PIL import Image, ImageDraw

    com_faixa = bool(titulo or preco)
    # com a faixa embaixo, a foto fica numa caixa menor e mais acima para não ficar escondida
    base = Image.open(io.BytesIO(imagem_host.preparar_story(dados, (980, 1100), 640) if com_faixa
                                 else imagem_host.preparar_story(dados))).convert("RGB")
    if com_faixa:
        tela = base.copy().convert("RGBA")
        camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(camada)
        fonte_t, fonte_p = _fonte(54), _fonte(110)
        linhas = _quebrar(d, (titulo or "").strip(), fonte_t, L - 120, 2) if titulo else []
        altura = 70 + len(linhas) * 66 + (140 if preco else 0) + 30
        topo = A - 150 - altura
        d.rounded_rectangle((40, topo, L - 40, A - 150), radius=40, fill=(10, 10, 20, 205))
        y = topo + 40
        for ln in linhas:
            d.text((80, y), ln, font=fonte_t, fill=(255, 255, 255, 255))
            y += 66
        if preco:
            d.text((80, y + 10), f"R$ {preco}", font=fonte_p, fill=(52, 211, 153, 255))
        base = Image.alpha_composite(tela, camada).convert("RGB")
    buf = io.BytesIO()
    base.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def gerar_de_fotos(fotos: list[dict], pasta) -> dict:
    """fotos: [{bytes, titulo?, preco?}] (1 a 10) → vídeo de 3 s por foto com zoom suave.
    → {video_url, capa_url, duracao}"""
    _exigir_ffmpeg()
    if not 1 <= len(fotos) <= MAX_FOTOS:
        raise ErroVideo(f"Escolha de 1 a {MAX_FOTOS} fotos para o vídeo.")
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    frames = SEGUNDOS_POR_FOTO * FPS
    with tempfile.TemporaryDirectory(dir=pasta) as tmp:
        tmp = Path(tmp)
        clipes = []
        for i, f in enumerate(fotos):
            try:
                jpg = quadro(f["bytes"], f.get("titulo"), f.get("preco"))
            except Exception as e:
                if isinstance(e, imagem_host.ErroImagem):
                    raise ErroVideo(str(e)) from None
                raise ErroVideo("Uma das fotos não pôde ser usada no vídeo.") from None
            img = tmp / f"f{i}.jpg"
            img.write_bytes(jpg)
            clipe = tmp / f"c{i}.mp4"
            zoom = (f"scale={L * 3 // 2}:{A * 3 // 2},zoompan=z='min(zoom+0.0007,1.1)':d={frames}:"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={L}x{A}:fps={FPS},format=yuv420p")
            _rodar(["ffmpeg", "-y", "-v", "error", "-i", str(img), "-vf", zoom, "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", str(clipe)])
            clipes.append(clipe)
            if i == 0:
                shutil.copy(img, tmp / "capa.jpg")
        lista = tmp / "lista.txt"
        lista.write_text("".join(f"file '{c.name}'\n" for c in clipes))
        nome = _novo_nome()
        saida = tmp / f"{nome}.mp4"
        _rodar(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy",
                "-movflags", "+faststart", str(saida)])
        shutil.move(str(saida), pasta / f"{nome}.mp4")
        shutil.move(str(tmp / "capa.jpg"), pasta / f"{nome}.jpg")
    return {"video_url": f"/midia/{nome}.mp4", "capa_url": f"/midia/{nome}.jpg",
            "duracao": float(len(fotos) * SEGUNDOS_POR_FOTO)}
