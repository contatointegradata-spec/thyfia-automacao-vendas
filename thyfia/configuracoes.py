"""Configurações da plataforma, editadas pelo admin na tela (sem mexer em .env).

Precedência: valor salvo na tela > variável de ambiente (usada como padrão/legado).
Segredos nunca voltam inteiros para o navegador (ver mascarado()).
"""

from __future__ import annotations

import os

from . import cofre, db

# chave → (variável de ambiente equivalente, é segredo?, rótulo)
CAMPOS: dict[str, tuple[str, bool, str]] = {
    "kiwify_token": ("KIWIFY_TOKEN", True, "Token do webhook da Kiwify"),
    "kiwify_produto_plus": ("KIWIFY_PRODUTO_PLUS", False, "Plano Plus na Kiwify (nome ou ID do plano; ou do produto)"),
    "kiwify_produto_pro": ("KIWIFY_PRODUTO_PRO", False, "Plano Pro na Kiwify (nome ou ID do plano; ou do produto)"),
    "kiwify_produto_premium": ("KIWIFY_PRODUTO_PREMIUM", False, "Plano Premium na Kiwify (nome ou ID do plano; ou do produto)"),
    "kiwify_link_plus": ("KIWIFY_LINK_PLUS", False, "Link de checkout — Plus"),
    "kiwify_link_pro": ("KIWIFY_LINK_PRO", False, "Link de checkout — Pro"),
    "kiwify_link_premium": ("KIWIFY_LINK_PREMIUM", False, "Link de checkout — Premium"),
    "n8n_webhook_url": ("N8N_WEBHOOK_URL", False, "Webhook do n8n que recebe os links"),
    "n8n_api_key": ("N8N_API_KEY", True, "Token do webhook do n8n (x-api-key)"),
    "evolution_url": ("EVOLUTION_URL", False, "URL da Evolution API (WhatsApp)"),
    "evolution_apikey": ("EVOLUTION_APIKEY", True, "Chave da Evolution API"),
    "evolution_instancia": ("EVOLUTION_INSTANCIA", False, "Instância da Evolution que envia as ofertas"),
    "telegram_bot": ("THYFIA_TELEGRAM_BOT", False, "@ do bot do Telegram da automação"),
    "telegram_bot_token": ("THYFIA_TELEGRAM_BOT_TOKEN", True, "Token do bot do Telegram (só para o teste de envio)"),
    "suporte_whatsapp": ("THYFIA_SUPORTE_WHATSAPP", False, "WhatsApp de suporte (só dígitos)"),
    "url_publica": ("THYFIA_URL_PUBLICA", False, "Endereço público da plataforma"),
}


def obter(chave: str) -> str:
    r = db.conn().execute("SELECT valor FROM configuracoes WHERE chave=?", (chave,)).fetchone()
    if r:
        v = r["valor"]
        return cofre.abrir(v[4:]) if v.startswith("enc:") else v
    env = CAMPOS.get(chave, ("", False, ""))[0]
    return (os.environ.get(env) or "").strip() if env else ""


def definir(chave: str, valor: str) -> None:
    if chave not in CAMPOS:
        raise ValueError("Configuração desconhecida.")
    valor = (valor or "").strip()
    c = db.conn()
    if not valor:
        c.execute("DELETE FROM configuracoes WHERE chave=?", (chave,))
    else:
        guardar = "enc:" + cofre.cifrar(valor) if CAMPOS[chave][1] else valor
        c.execute("INSERT INTO configuracoes (chave, valor) VALUES (?,?) ON CONFLICT(chave) DO UPDATE "
                  "SET valor=excluded.valor, atualizado=datetime('now','localtime')", (chave, guardar))
    c.commit()


def listar_para_admin() -> list[dict]:
    out = []
    for chave, (_, segredo, rotulo) in CAMPOS.items():
        v = obter(chave)
        out.append({"chave": chave, "rotulo": rotulo, "segredo": segredo, "definido": bool(v),
                    "valor": "" if segredo else v, "mascarado": cofre.mascarar(v) if segredo and v else ""})
    return out
