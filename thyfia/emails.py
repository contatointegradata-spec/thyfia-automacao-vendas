"""E-mail de boas-vindas da ThyfIA: montagem (HTML no Design System + texto) e envio pelo Gmail.

O remetente e a senha de app do Gmail são configurados pelo admin na tela (Configurações). Sem eles
configurados, nada é enviado e o cadastro segue normal. O envio nunca derruba o cadastro: roda em
segundo plano e os erros só vão para o log.
"""

from __future__ import annotations

import html as _html
import logging
import re
import smtplib
import ssl
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from . import auth, configuracoes, planos

log = logging.getLogger("thyfia")

HOST_SMTP, PORTA_SMTP, PORTA_STARTTLS = "smtp.gmail.com", 465, 587
URL_PADRAO = "https://thyfia.integradata.app.br"
ASSETS = Path(__file__).parent / "static" / "marca" / "email"
LIMITE_POR_HORA = 40  # trava contra abuso do cadastro aberto (cada cadastro dispara 1 e-mail)

LIMITE_REDEFINICAO_POR_HORA = 60  # teto geral de e-mails de redefinição (além do limite por conta em auth)

_enviados: deque[float] = deque()
_enviados_redef: deque[float] = deque()
_trava = threading.Lock()

_RESUMO_PLANO = {"plus": "Links e planilha", "pro": "+ envio automático", "premium": "+ posts no Instagram"}


# --------------------------------------------------------------------------- configuração
def remetente() -> str:
    return configuracoes.obter("email_remetente")


def configurado() -> bool:
    return bool(remetente() and configuracoes.obter("email_senha_app"))


def _url_site() -> str:
    return (configuracoes.obter("url_publica") or URL_PADRAO).rstrip("/")


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _primeiro_nome(nome: str) -> str:
    p = (nome or "").strip().split()
    return p[0] if p else "tudo bem"


# --------------------------------------------------------------------------- conteúdo
_ESTILO = """
@font-face{font-family:'Fredoka';font-weight:600;src:url('__SITE__/static/fonts/fredoka-latin-600-normal.woff2') format('woff2')}
@font-face{font-family:'Fredoka';font-weight:700;src:url('__SITE__/static/fonts/fredoka-latin-700-normal.woff2') format('woff2')}
@font-face{font-family:'DM Sans';font-weight:400;src:url('__SITE__/static/fonts/dm-sans-latin-400-normal.woff2') format('woff2')}
@font-face{font-family:'DM Sans';font-weight:700;src:url('__SITE__/static/fonts/dm-sans-latin-700-normal.woff2') format('woff2')}
body{margin:0;padding:0;background:#07070F}
a{color:#A78BFA}
@media (max-width:620px){
  .caixa{width:100%!important}
  .pad{padding-left:20px!important;padding-right:20px!important}
  .h1{font-size:30px!important;line-height:36px!important}
  .plano{display:block!important;width:100%!important;padding:0 0 12px 0!important}
}
"""

_TITULO = "font-family:'Fredoka','Trebuchet MS','Arial Rounded MT Bold',Verdana,sans-serif;font-weight:700;color:#F2F1FB"
_TEXTO = "font-family:'DM Sans',Helvetica,Arial,sans-serif;color:#F2F1FB"
_MONO = "font-family:'JetBrains Mono',Menlo,Consolas,'Courier New',monospace"


def _passos() -> list[tuple[str, str, str]]:
    return [
        ("1", "Escolha o seu plano", "Em <b>Meu plano</b>. O pagamento é pela Kiwify e libera tudo sozinho."),
        ("2", "Conecte o Mercado Livre", "Em <b>Integrações</b>, uma vez só."),
        ("3", "Gere seus links", "Digite o que você divulga (ex.: <i>capinha iphone</i>) e baixe a planilha."),
    ]


def _cartao_plano(p: dict) -> str:
    destaque = p.get("destaque")
    borda = "#B9A9FA" if destaque else "#6F6D9A"
    selo = ('<div style="margin:0 0 8px 0"><span style="display:inline-block;background:#7B4DEF;color:#ffffff;'
            'border-radius:20px;padding:3px 10px;font:700 11px/14px Helvetica,Arial,sans-serif;letter-spacing:1px">DESTAQUE</span></div>'
            if destaque else '<div style="height:25px;line-height:25px;font-size:1px">&nbsp;</div>')
    return (f'<td class="plano" width="33%" valign="top" style="padding:0 5px">'
            f'<table role="presentation" width="100%" height="132" cellpadding="0" cellspacing="0" style="background:#151528;border:2px solid {borda};'
            f'border-radius:18px;box-shadow:0 4px 0 #3B1FA8"><tr><td align="center" style="padding:16px 10px">{selo}'
            f'<div style="{_TITULO};font-size:20px;line-height:24px">{_html.escape(p["nome"])}</div>'
            f'<div style="{_MONO};font-weight:500;font-size:17px;line-height:30px;color:#22D3EE">{_brl(p["preco"])}<span style="font-size:12px;color:#A6A4C2">/mês</span></div>'
            f'<div style="{_TEXTO};font-size:13px;line-height:18px;color:#A6A4C2">{_html.escape(_RESUMO_PLANO.get(p["id"], p["resumo"]))}</div>'
            f'</td></tr></table></td>')


def _casca(titulo: str, preheader: str, miolo: str, rodape: str) -> str:
    """Moldura comum dos e-mails: fundo, logo no topo, o miolo e o rodapé."""
    site = _url_site()
    estilo = _ESTILO.replace("__SITE__", site)
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><meta name="supported-color-schemes" content="dark light">
<title>{_html.escape(titulo)}</title><style>{estilo}</style></head>
<body style="margin:0;padding:0;background:#07070F">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#07070F">{_html.escape(preheader)}&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#07070F" style="background:#07070F"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" class="caixa" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px">

<tr><td align="center" style="padding:0 0 22px 0"><a href="{site}"><img src="cid:logo.thyfia" width="170" alt="ThyfIA" style="display:block;border:0;height:auto"></a></td></tr>

{miolo}

<tr><td align="center" style="padding:30px 10px 0 10px;{_TEXTO};font-size:12px;line-height:18px;color:#6F6D9A">{rodape}</td></tr>

</table></td></tr></table></body></html>"""


def _botao(url: str, texto: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td align="center" bgcolor="#7B4DEF" style="background:#7B4DEF;'
            f'border:2px solid #B9A9FA;border-radius:14px;box-shadow:0 5px 0 #3B1FA8"><a href="{_html.escape(url)}" style="display:inline-block;'
            f'padding:15px 34px;{_TEXTO};font-weight:700;font-size:17px;line-height:22px;color:#ffffff;text-decoration:none">{_html.escape(texto)}</a></td></tr></table>')


_ESPACO = '<tr><td style="height:26px;line-height:26px;font-size:1px">&nbsp;</td></tr>'


def montar_html(nome: str) -> str:
    site = _url_site()
    ola = _html.escape(_primeiro_nome(nome))
    host = _html.escape(site.replace("https://", "").replace("http://", ""))
    passos = "".join(
        f'<tr><td valign="top" width="52" style="padding:0 0 18px 0">'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td align="center" width="40" height="40" bgcolor="#7B4DEF" '
        f'style="background:#7B4DEF;border:2px solid #B9A9FA;border-radius:12px;box-shadow:0 3px 0 #3B1FA8;color:#ffffff;{_MONO};font-weight:500;font-size:18px;line-height:36px">{n}</td></tr></table></td>'
        f'<td valign="top" style="padding:0 0 18px 0"><div style="{_TITULO};font-size:19px;line-height:24px">{t}</div>'
        f'<div style="{_TEXTO};font-size:15px;line-height:22px;color:#A6A4C2;padding-top:2px">{d}</div></td></tr>'
        for n, t, d in _passos())
    cartoes = "".join(_cartao_plano(planos.PLANOS[i]) for i in planos.ORDEM)
    zap = re.sub(r"\D", "", configuracoes.obter("suporte_whatsapp"))
    ajuda_zap = (f' Prefere o WhatsApp? <a href="https://wa.me/{zap}" style="color:#A78BFA;font-weight:700">Fale com a gente</a>.' if zap else "")
    miolo = f"""<tr><td class="pad" align="center" bgcolor="#151528" style="background:#151528;border:2px solid #B9A9FA;border-radius:28px;box-shadow:0 8px 0 #3B1FA8;padding:34px 32px 32px 32px">
  <img src="cid:thyf.thyfia" width="190" alt="Thyf, o gatinho da ThyfIA" style="display:block;border:0;height:auto;margin:0 auto 10px auto">
  <div class="h1" style="{_TITULO};font-size:36px;line-height:42px">Oi, {ola}!<br><span style="color:#A78BFA">Bem-vindo à ThyfIA</span></div>
  <div style="{_TEXTO};font-size:17px;line-height:26px;color:#A6A4C2;padding:14px 0 24px 0">Eu sou o Thyf. Acho a oferta, monto a mensagem e te ajudo a levar seus links de afiliado até o grupo certo.</div>
  {_botao(site, "Entrar na ThyfIA")}
</td></tr>

{_ESPACO}

<tr><td class="pad" bgcolor="#0B0B17" style="background:#0B0B17;border:2px solid #24243F;border-radius:24px;padding:28px 28px 10px 28px">
  <div style="{_TITULO};font-size:24px;line-height:30px;padding-bottom:18px">Seus 3 primeiros passos</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{passos}</table>
</td></tr>

{_ESPACO}

<tr><td class="pad" style="padding:0">
  <div style="{_TITULO};font-size:24px;line-height:30px;padding:0 4px 14px 4px">Planos, sem enrolação</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{cartoes}</tr></table>
  <div style="{_TEXTO};font-size:13px;line-height:19px;color:#A6A4C2;padding:14px 4px 0 4px">Assinatura mensal, paga pela Kiwify. Dá para começar pelo Plus e fazer upgrade quando quiser.</div>
</td></tr>

{_ESPACO}

<tr><td class="pad" bgcolor="#2A1F5C" style="background:#2A1F5C;border:2px solid #B9A9FA;border-radius:20px;padding:20px 24px">
  <div style="{_TEXTO};font-size:15px;line-height:23px"><b>Sem promessa de ganho.</b> A ThyfIA organiza e envia as ofertas. O resultado depende das ofertas que você escolhe e do seu público.</div>
</td></tr>

{_ESPACO}

<tr><td class="pad" style="padding:0 4px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td valign="middle" width="84"><img src="cid:thyf2.thyfia" width="72" alt="" style="display:block;border:0;height:auto"></td>
    <td valign="middle" style="{_TEXTO};font-size:15px;line-height:23px;color:#A6A4C2"><b style="color:#F2F1FB">Ficou com dúvida?</b> É só responder este e-mail que a gente te ajuda.{ajuda_zap}</td>
  </tr></table>
</td></tr>"""
    rodape = f"Você recebeu este e-mail porque criou uma conta na ThyfIA em {host}.<br>É um e-mail único de boas-vindas.<br>ThyfIA · Integradata"
    return _casca("Bem-vindo à ThyfIA", "Seu cadastro está pronto. Veja os 3 passos para gerar seus primeiros links.", miolo, rodape)


def montar_html_redefinicao(nome: str, link: str) -> str:
    ola = _html.escape(_primeiro_nome(nome))
    minutos = int(auth.DURACAO_REDEFINICAO.total_seconds() // 60)
    miolo = f"""<tr><td class="pad" align="center" bgcolor="#151528" style="background:#151528;border:2px solid #B9A9FA;border-radius:28px;box-shadow:0 8px 0 #3B1FA8;padding:34px 32px 32px 32px">
  <img src="cid:thyf3.thyfia" width="170" alt="Thyf pensando" style="display:block;border:0;height:auto;margin:0 auto 10px auto">
  <div class="h1" style="{_TITULO};font-size:34px;line-height:40px">Vamos criar uma<br><span style="color:#A78BFA">nova senha</span></div>
  <div style="{_TEXTO};font-size:17px;line-height:26px;color:#A6A4C2;padding:14px 0 24px 0">Oi, {ola}! Pediram para redefinir a senha da sua conta na ThyfIA. Se foi você, é só tocar no botão.</div>
  {_botao(link, "Criar nova senha")}
  <div style="{_TEXTO};font-size:14px;line-height:21px;color:#A6A4C2;padding-top:22px">O link vale por <b style="color:#F2F1FB">{minutos} minutos</b> e só funciona uma vez.</div>
</td></tr>

{_ESPACO}

<tr><td class="pad" bgcolor="#2A1F5C" style="background:#2A1F5C;border:2px solid #B9A9FA;border-radius:20px;padding:20px 24px">
  <div style="{_TEXTO};font-size:15px;line-height:23px"><b>Não foi você?</b> Ignore este e-mail. Sua senha continua a mesma e nada muda na sua conta.</div>
</td></tr>

{_ESPACO}

<tr><td class="pad" style="padding:0 4px">
  <div style="{_TEXTO};font-size:13px;line-height:20px;color:#A6A4C2">Se o botão não abrir, copie este endereço e cole no navegador:</div>
  <div style="{_MONO};font-size:12px;line-height:18px;color:#A78BFA;word-break:break-all;padding-top:6px">{_html.escape(link)}</div>
</td></tr>"""
    rodape = "Você recebeu este e-mail porque alguém pediu a redefinição de senha de uma conta na ThyfIA com este endereço.<br>ThyfIA · Integradata"
    return _casca("Criar nova senha", "O link para criar uma nova senha vale por 60 minutos.", miolo, rodape)


def montar_texto_redefinicao(nome: str, link: str) -> str:
    minutos = int(auth.DURACAO_REDEFINICAO.total_seconds() // 60)
    return (f"Oi, {_primeiro_nome(nome)}!\n\nPediram para redefinir a senha da sua conta na ThyfIA. Se foi você, abra o link abaixo e crie uma nova senha:\n\n"
            f"{link}\n\nO link vale por {minutos} minutos e só funciona uma vez.\n\n"
            "Não foi você? Ignore este e-mail. Sua senha continua a mesma e nada muda na sua conta.\n\nThyfIA · Integradata\n")


def montar_texto(nome: str) -> str:
    site = _url_site()
    precos = "\n".join(f"- {planos.PLANOS[i]['nome']}: {_brl(planos.PLANOS[i]['preco'])}/mês ({_RESUMO_PLANO.get(i, '')})" for i in planos.ORDEM)
    return (f"Oi, {_primeiro_nome(nome)}! Bem-vindo à ThyfIA.\n\n"
            "Eu sou o Thyf. Acho a oferta, monto a mensagem e te ajudo a levar seus links de afiliado até o grupo certo.\n\n"
            "Seus 3 primeiros passos:\n"
            "1. Escolha o seu plano, em Meu plano (o pagamento é pela Kiwify e libera tudo sozinho).\n"
            "2. Conecte o Mercado Livre, em Integrações (uma vez só).\n"
            "3. Gere seus links: digite o que você divulga (ex.: capinha iphone) e baixe a planilha.\n\n"
            f"Planos (assinatura mensal):\n{precos}\n\n"
            "Sem promessa de ganho: a ThyfIA organiza e envia as ofertas, e o resultado depende das ofertas que você escolhe e do seu público.\n\n"
            f"Entrar na ThyfIA: {site}\n\n"
            "Ficou com dúvida? É só responder este e-mail.\n\n"
            "Você recebeu este e-mail porque criou uma conta na ThyfIA. É um e-mail único de boas-vindas.\nThyfIA · Integradata\n")


def _empacotar(assunto: str, nome: str, destino: str, texto: str, html: str, imagens: tuple) -> EmailMessage:
    de = remetente()
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("ThyfIA", de))
    msg["To"] = formataddr((nome.strip(), destino)) if nome.strip() else destino
    msg["Reply-To"] = de
    msg["Message-ID"] = make_msgid(domain="thyfia.integradata.app.br")
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    corpo = msg.get_body(preferencelist=("html",))
    for cid, arq in imagens:
        corpo.add_related((ASSETS / arq).read_bytes(), "image", "png", cid=f"<{cid}>")
    return msg


def montar_mensagem(nome: str, destino: str) -> EmailMessage:
    return _empacotar(f"{_primeiro_nome(nome)}, bem-vindo à ThyfIA! Seus 3 primeiros passos", nome, destino,
                      montar_texto(nome), montar_html(nome),
                      (("logo.thyfia", "logo.png"), ("thyf.thyfia", "thyf-feliz.png"), ("thyf2.thyfia", "thyf-piscando.png")))


def link_redefinicao(token: str) -> str:
    return f"{_url_site()}/?redefinir={token}"


def montar_mensagem_redefinicao(nome: str, destino: str, token: str) -> EmailMessage:
    link = link_redefinicao(token)
    return _empacotar("Crie uma nova senha na ThyfIA", nome, destino,
                      montar_texto_redefinicao(nome, link), montar_html_redefinicao(nome, link),
                      (("logo.thyfia", "logo.png"), ("thyf3.thyfia", "thyf-pensando.png")))


# --------------------------------------------------------------------------- envio
def _detalhe(e: Exception) -> str:
    """Nome do erro + texto curto, para o admin (e o log) saberem a causa real. Nunca inclui a senha."""
    txt = " ".join(str(e).split())[:160]
    return f"{type(e).__name__}: {txt}" if txt else type(e).__name__


def _traduzir_erro(e: Exception) -> str:
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("O Gmail recusou o login. Use uma senha de app (16 letras, gerada em Conta Google > Segurança > "
                "Verificação em duas etapas > Senhas de app), não a senha normal da conta, e confira se o e-mail "
                "remetente é o mesmo da conta que gerou a senha.")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return "O Gmail recusou o destinatário."
    if isinstance(e, ssl.SSLError):
        return f"Falha na conexão segura com o Gmail ({_detalhe(e)})."
    if isinstance(e, smtplib.SMTPException):
        return f"O Gmail respondeu com erro ({_detalhe(e)})."
    if isinstance(e, (TimeoutError, OSError)):
        return (f"Não consegui conectar ao Gmail ({_detalhe(e)}). Se persistir, o servidor pode estar bloqueando a saída "
                f"nas portas {PORTA_SMTP} e {PORTA_STARTTLS}.")
    return f"Falha ao enviar: {_detalhe(e)}"


def _senha_app() -> str:
    # o Google mostra a senha em 4 grupos separados por espaço (às vezes espaço não separável): tudo isso sai
    return re.sub(r"\s+", "", configuracoes.obter("email_senha_app"))


def _enviar_ssl(msg: EmailMessage) -> None:
    with smtplib.SMTP_SSL(HOST_SMTP, PORTA_SMTP, timeout=20, context=ssl.create_default_context()) as s:
        s.login(remetente(), _senha_app())
        s.send_message(msg)


def _enviar_starttls(msg: EmailMessage) -> None:
    with smtplib.SMTP(HOST_SMTP, PORTA_STARTTLS, timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(remetente(), _senha_app())
        s.send_message(msg)


def enviar(msg: EmailMessage) -> None:
    """Envia pelo Gmail (SSL na 465; se a conexão falhar, tenta STARTTLS na 587). Levanta ValueError amigável."""
    if not configurado():
        raise ValueError("Preencha o e-mail remetente e a senha de app do Gmail em Configurações.")
    try:
        _enviar_ssl(msg)
        return
    except Exception as e1:  # noqa: BLE001
        # só vale tentar outra porta quando nem chegamos a conversar com o Gmail (rede/TLS), não em erro de login
        if isinstance(e1, smtplib.SMTPException) and not isinstance(e1, smtplib.SMTPServerDisconnected):
            log.warning("e-mail: falha SMTP na porta %s: %s", PORTA_SMTP, _detalhe(e1))
            raise ValueError(_traduzir_erro(e1)) from e1
        log.warning("e-mail: porta %s falhou (%s); tentando a %s", PORTA_SMTP, _detalhe(e1), PORTA_STARTTLS)
        try:
            _enviar_starttls(msg)
            return
        except Exception as e2:  # noqa: BLE001
            log.warning("e-mail: porta %s também falhou: %s", PORTA_STARTTLS, _detalhe(e2))
            raise ValueError(_traduzir_erro(e2 if not isinstance(e2, smtplib.SMTPServerDisconnected) else e1)) from e2


def _dentro_do_limite(fila: deque | None = None, limite: int | None = None) -> bool:
    fila = _enviados if fila is None else fila
    limite = LIMITE_POR_HORA if limite is None else limite
    agora = time.time()
    with _trava:
        while fila and agora - fila[0] > 3600:
            fila.popleft()
        if len(fila) >= limite:
            return False
        fila.append(agora)
        return True


def enviar_boas_vindas(nome: str, destino: str) -> bool:
    """Chamado em segundo plano após o cadastro. Nunca levanta: devolve True se enviou."""
    try:
        if not configurado():
            return False
        if not _dentro_do_limite():
            log.warning("e-mail de boas-vindas não enviado: limite de %s/hora atingido", LIMITE_POR_HORA)
            return False
        enviar(montar_mensagem(nome, destino))
        log.info("e-mail de boas-vindas enviado")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("e-mail de boas-vindas falhou: %s", e)
        return False


def enviar_em_segundo_plano(nome: str, destino: str) -> None:
    threading.Thread(target=enviar_boas_vindas, args=(nome, destino), daemon=True).start()


def enviar_redefinicao(nome: str, destino: str, token: str) -> bool:
    """Manda o link de redefinição de senha. Nunca levanta: devolve True se enviou."""
    try:
        if not configurado():
            log.warning("redefinição de senha pedida, mas o e-mail da plataforma não está configurado (Admin > Configurações)")
            return False
        if not _dentro_do_limite(_enviados_redef, LIMITE_REDEFINICAO_POR_HORA):
            log.warning("e-mail de redefinição não enviado: limite de %s/hora atingido", LIMITE_REDEFINICAO_POR_HORA)
            return False
        enviar(montar_mensagem_redefinicao(nome, destino, token))
        log.info("e-mail de redefinição de senha enviado")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("e-mail de redefinição falhou: %s", e)
        return False


def enviar_redefinicao_em_segundo_plano(nome: str, destino: str, token: str) -> None:
    threading.Thread(target=enviar_redefinicao, args=(nome, destino, token), daemon=True).start()
