# ThyfIA em produção (VPS). Inclui Google Chrome para a geração de links do Mercado Livre.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Sao_Paulo \
    THYFIA_HEADLESS=1 \
    THYFIA_COOKIE_SECURE=1 \
    THYFIA_DADOS=/app/dados \
    THYFIA_CHROME=/usr/bin/google-chrome-stable

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg fonts-liberation tzdata libatomic1 \
 && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
# falha cedo no build (e não só na 1ª geração) se o driver do Playwright não consegue rodar
RUN python -c "import playwright, subprocess, pathlib; d = pathlib.Path(playwright.__file__).parent / \"driver\" / \"node\"; print(subprocess.run([str(d), \"--version\"], capture_output=True, text=True, check=True).stdout)"

COPY gerar_links_afiliado.py ./
COPY thyfia ./thyfia
COPY extensao_thyfia ./extensao_thyfia

# banco, perfis do Chrome e fotos ficam no volume (não se perdem a cada deploy)
VOLUME ["/app/dados"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/ >/dev/null || exit 1

CMD ["python", "-m", "uvicorn", "thyfia.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
