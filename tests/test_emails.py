"""E-mail de boas-vindas: montagem (HTML/texto/imagens), envio pelo Gmail (mockado) e ligação ao cadastro."""

from __future__ import annotations

import re
import smtplib
import ssl
import unittest
from unittest import mock

from fastapi import HTTPException

from thyfia import auth, configuracoes, db, emails, planos
from thyfia import app as app_mod


def zerar():
    c = db.conn()
    c.execute("DELETE FROM configuracoes")
    c.commit()
    emails._enviados.clear()


def configurar():
    configuracoes.definir("email_remetente", "contato.thyfia@gmail.com")
    configuracoes.definir("email_senha_app", "abcdefghijklmnop")


class TestMontagem(unittest.TestCase):
    def setUp(self):
        zerar()
        configurar()

    def test_html_tem_nome_precos_do_catalogo_e_sem_promessa_de_ganho(self):
        h = emails.montar_html("Kelven Pereira da Silva")
        self.assertIn("Oi, Kelven!", h)
        for pid in planos.ORDEM:
            self.assertIn(emails._brl(planos.PLANOS[pid]["preco"]), h)
        self.assertIn("19,90", h)
        self.assertIn("Sem promessa de ganho", h)
        self.assertNotRegex(h.lower(), r"fique rico|renda garantida|ganhe dormindo")

    def test_nome_e_escapado_no_html(self):
        h = emails.montar_html('<script>alert(1)</script> Silva')
        self.assertNotIn("<script>alert", h)
        self.assertIn("&lt;script&gt;", h)

    def test_sem_emoji_no_visual_do_email(self):
        for t in (emails.montar_html("Ana"), emails.montar_texto("Ana")):
            self.assertIsNone(re.search("[\U0001F300-\U0001FAFF☀-➿]", t))

    def test_mensagem_tem_texto_html_e_todas_as_imagens_referenciadas(self):
        m = emails.montar_mensagem("Ana Souza", "ana@exemplo.com")
        self.assertEqual(m["To"], "Ana Souza <ana@exemplo.com>")
        self.assertIn("contato.thyfia@gmail.com", m["From"])
        self.assertEqual(m["Reply-To"], "contato.thyfia@gmail.com")
        tipos = [p.get_content_type() for p in m.walk()]
        self.assertIn("text/plain", tipos)
        self.assertIn("text/html", tipos)
        html = m.get_body(("html",)).get_content()
        cids = set(re.findall(r"cid:([\w.]+)", html))
        anexados = {p["Content-ID"].strip("<>") for p in m.walk() if p.get_content_type() == "image/png"}
        self.assertTrue(cids and cids <= anexados, (cids, anexados))

    def test_link_usa_a_url_publica_configurada_e_whatsapp_de_suporte(self):
        configuracoes.definir("url_publica", "https://exemplo.test/")
        configuracoes.definir("suporte_whatsapp", "+55 (34) 99999-0000")
        h = emails.montar_html("Ana")
        self.assertIn('href="https://exemplo.test"', h)
        self.assertIn("https://wa.me/5534999990000", h)

    def test_senha_de_app_fica_cifrada_no_banco(self):
        bruto = db.conn().execute("SELECT valor FROM configuracoes WHERE chave='email_senha_app'").fetchone()[0]
        self.assertTrue(bruto.startswith("enc:"))
        self.assertNotIn("abcdefghijklmnop", bruto)


class TestResend(unittest.TestCase):
    def setUp(self):
        zerar()
        configuracoes.definir("email_remetente", "contato@integradata.app.br")
        configuracoes.definir("email_resend_chave", "re_chave_de_teste")

    def _resp(self, status=200, corpo=None):
        r = mock.Mock(status_code=status, ok=status < 400, text="")
        r.json.return_value = corpo if corpo is not None else {"id": "abc"}
        return r

    def test_so_a_chave_do_resend_ja_conta_como_configurado(self):
        self.assertTrue(emails.configurado())
        configuracoes.definir("email_resend_chave", "")
        self.assertFalse(emails.configurado())  # sem Resend e sem senha de app

    def test_envia_pela_api_com_https_e_nao_abre_smtp(self):
        with mock.patch("requests.post", return_value=self._resp()) as post, \
             mock.patch("smtplib.SMTP_SSL") as smtp, mock.patch("smtplib.SMTP") as smtp2:
            emails.enviar(emails.montar_mensagem("Ana Souza", "ana@exemplo.com"))
        smtp.assert_not_called(); smtp2.assert_not_called()
        self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer re_chave_de_teste")
        corpo = post.call_args.kwargs["json"]
        self.assertEqual(corpo["from"], "ThyfIA <contato@integradata.app.br>")
        self.assertEqual(corpo["to"], ["Ana Souza <ana@exemplo.com>"])
        self.assertIn("bem-vindo", corpo["subject"].lower())
        self.assertEqual(corpo["reply_to"], "contato@integradata.app.br")
        self.assertTrue(corpo["text"].strip())

    def test_imagens_cid_viram_links_do_site(self):
        with mock.patch("requests.post", return_value=self._resp()) as post:
            emails.enviar(emails.montar_mensagem_redefinicao("Ana", "ana@exemplo.com", "TOK"))
        html = post.call_args.kwargs["json"]["html"]
        self.assertNotIn("cid:", html)
        self.assertIn("https://thyfia.integradata.app.br/static/marca/email/logo.png", html)
        self.assertIn("/static/marca/email/thyf-pensando.png", html)
        self.assertIn("?redefinir=TOK", html)

    def test_resend_tem_prioridade_sobre_o_gmail(self):
        configuracoes.definir("email_senha_app", "abcdefghijklmnop")
        with mock.patch("requests.post", return_value=self._resp()) as post, mock.patch("smtplib.SMTP_SSL") as smtp:
            emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        post.assert_called_once(); smtp.assert_not_called()

    def test_chave_recusada_vira_mensagem_clara(self):
        with mock.patch("requests.post", return_value=self._resp(401, {"message": "API key is invalid"})):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("chave de API", str(cm.exception))
        self.assertNotIn("re_chave_de_teste", str(cm.exception))

    def test_dominio_nao_verificado_mostra_o_motivo(self):
        with mock.patch("requests.post", return_value=self._resp(403, {"message": "The integradata.app.br domain is not verified."})):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("not verified", str(cm.exception))
        self.assertIn("domínio", str(cm.exception))

    def test_limite_de_taxa_e_falha_de_rede(self):
        with mock.patch("requests.post", return_value=self._resp(429, {"message": "slow down"})):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("esperar", str(cm.exception))
        import requests as rq
        with mock.patch("requests.post", side_effect=rq.ConnectionError("sem rede")):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("Resend", str(cm.exception))

    def test_boas_vindas_nunca_levanta_se_o_resend_falhar(self):
        with mock.patch("requests.post", return_value=self._resp(500, {"message": "erro"})):
            self.assertFalse(emails.enviar_boas_vindas("Ana", "ana@exemplo.com"))


class TestEnvio(unittest.TestCase):
    def setUp(self):
        zerar()

    def test_sem_configuracao_nao_envia_nada(self):
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            self.assertFalse(emails.enviar_boas_vindas("Ana", "ana@exemplo.com"))
        smtp.assert_not_called()

    def test_envia_pelo_gmail_com_login(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            self.assertTrue(emails.enviar_boas_vindas("Ana", "ana@exemplo.com"))
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args[:2], ("smtp.gmail.com", 465))
        s = smtp.return_value.__enter__.return_value
        s.login.assert_called_once_with("contato.thyfia@gmail.com", "abcdefghijklmnop")
        s.send_message.assert_called_once()

    def test_falha_do_gmail_nao_levanta_no_cadastro(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL", side_effect=OSError("sem rede")):
            self.assertFalse(emails.enviar_boas_vindas("Ana", "ana@exemplo.com"))

    def test_login_recusado_vira_mensagem_sobre_senha_de_app(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("senha de app", str(cm.exception))

    def test_senha_de_app_colada_com_espacos_e_normalizada(self):
        configurar()
        configuracoes.definir("email_senha_app", "abcd efgh\u00a0ijkl mnop")
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        smtp.return_value.__enter__.return_value.login.assert_called_once_with("contato.thyfia@gmail.com", "abcdefghijklmnop")

    def test_porta_465_bloqueada_cai_para_starttls_587(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL", side_effect=TimeoutError("timed out")), mock.patch("smtplib.SMTP") as plano_b:
            emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertEqual(plano_b.call_args.args[:2], ("smtp.gmail.com", 587))
        s = plano_b.return_value.__enter__.return_value
        s.starttls.assert_called_once()
        s.login.assert_called_once_with("contato.thyfia@gmail.com", "abcdefghijklmnop")
        s.send_message.assert_called_once()

    def test_login_recusado_nao_tenta_outra_porta(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL") as smtp, mock.patch("smtplib.SMTP") as plano_b:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
            with self.assertRaises(ValueError):
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        plano_b.assert_not_called()

    def test_erro_de_rede_mostra_a_causa_real_sem_vazar_a_senha(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("recusado")), \
             mock.patch("smtplib.SMTP", side_effect=OSError("Network is unreachable")):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        msg = str(cm.exception)
        self.assertIn("Network is unreachable", msg)
        self.assertIn("587", msg)
        self.assertNotIn("abcdefghijklmnop", msg)

    def test_falha_de_tls_e_explicada(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL", side_effect=ssl.SSLError("certificate verify failed")), \
             mock.patch("smtplib.SMTP", side_effect=ssl.SSLError("certificate verify failed")):
            with self.assertRaises(ValueError) as cm:
                emails.enviar(emails.montar_mensagem("Ana", "ana@exemplo.com"))
        self.assertIn("conexão segura", str(cm.exception))

    def test_limite_por_hora_trava_excesso(self):
        configurar()
        with mock.patch("smtplib.SMTP_SSL") as smtp, mock.patch.object(emails, "LIMITE_POR_HORA", 2):
            r = [emails.enviar_boas_vindas("Ana", f"a{i}@exemplo.com") for i in range(3)]
        self.assertEqual(r, [True, True, False])
        self.assertEqual(smtp.call_count, 2)


class TestLigacoes(unittest.TestCase):
    def setUp(self):
        zerar()

    def test_cadastro_dispara_boas_vindas_com_email_em_minusculas(self):
        with mock.patch.object(emails, "enviar_em_segundo_plano") as env:
            app_mod.cadastro(app_mod.Cadastro(nome="Bia Lima", email=f"Bia{id(self)}@Exemplo.com", senha="senha123"), app_mod.Response())
        env.assert_called_once_with("Bia Lima", f"bia{id(self)}@exemplo.com")

    def test_cadastro_com_email_repetido_nao_envia(self):
        mail = f"rep{id(self)}@exemplo.com"
        auth.criar_usuario("Rep", mail, "senha123")
        with mock.patch.object(emails, "enviar_em_segundo_plano") as env:
            with self.assertRaises(HTTPException):
                app_mod.cadastro(app_mod.Cadastro(nome="Rep", email=mail, senha="senha123"), app_mod.Response())
        env.assert_not_called()

    def test_teste_do_admin_manda_para_ele_mesmo(self):
        configurar()
        adm = {"nome": "Admin Teste", "email": "admin@exemplo.com"}
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            r = app_mod.admin_testar_email(u=adm)
        self.assertEqual(r["para"], "admin@exemplo.com")
        msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertIn("admin@exemplo.com", msg["To"])

    def test_teste_do_admin_sem_configuracao_da_erro_claro(self):
        with self.assertRaises(HTTPException) as cm:
            app_mod.admin_testar_email(u={"nome": "A", "email": "a@exemplo.com"})
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("senha de app", cm.exception.detail)


if __name__ == "__main__":
    unittest.main()
