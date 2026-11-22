"""Isola os testes: banco/planilhas numa pasta temporária e webhook falso.

Roda antes de qualquer `import thyfia`, então os testes nunca tocam em dados/thyfia.db,
nas planilhas reais nem no n8n de verdade (load_dotenv não sobrescreve variáveis já definidas).
"""

import os
import tempfile

os.environ["THYFIA_DADOS"] = tempfile.mkdtemp(prefix="thyfia-testes-")
os.environ["N8N_WEBHOOK_URL"] = "https://n8n.exemplo.test/webhook/thyfia-links"
os.environ["N8N_API_KEY"] = "token-de-teste-123"
os.environ["THYFIA_LIMPAR_PLANILHAS_DIAS"] = "0"
os.environ["THYFIA_AGENDADOR"] = "0"
os.environ["THYFIA_PILOTO"] = "0"
# Instagram falso: definido aqui para o load_dotenv nunca trazer o token real do .env
os.environ["IG_GRAPH_HOST"] = "https://graph.facebook.com"
os.environ["IG_GRAPH_VERSION"] = "v21.0"
os.environ["IG_IMAGE_HOST"] = "ml_direct"
os.environ["THYFIA_URL_PUBLICA"] = ""
os.environ["IG_ACCESS_TOKEN"] = "IGTOKEN-SECRETO-DE-TESTE-999"
os.environ["IG_USER_ID"] = "17841400000000000"
# testes usam as credenciais acima como se fossem de qualquer cliente (em produção só o admin usa o .env)
os.environ["THYFIA_IG_ENV_FALLBACK"] = "1"
