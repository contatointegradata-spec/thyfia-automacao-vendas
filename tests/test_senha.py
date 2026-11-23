"""Esqueci minha senha: token seguro (hash, expiração, uso único), sem revelar contas, sessões encerradas,
e-mail de redefinição e telas do front."""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from thyfia import auth, configuracoes, db, emails
from thyfia import app as app_mod

ESTATICO = Path(__file__).resolve().parents[1] / "thyfia" / "static"


def limpar():
    c = db.conn()
    c.execute("DELETE FROM redefinicoes_senha")
    c.execute("DELETE FROM sessoes")
    c.execute("DELETE FROM usuarios WHERE email LIKE '%@senha.test'")
    c.execute("DELETE FROM configuracoes")
    c.commit()
    emails._enviados_redef.clear()


def criar(email="ana@senha.test", senha="senhaantiga1"):
    return auth.criar_usuario("Ana Souza", email, senha)


class TestToken(unittest.TestCase):
    def setUp(self):
        limpar()

    def test_pedido_guarda_so_o_hash_do_token(self):
        criar()
        nome, email, token = auth.pedir_redefinicao("ANA@senha.test ")
        self.assertEqual((nome, email), ("Ana Souza", "ana@senha.test"))
        linhas = db.conn().execute("SELECT token_hash FROM redefinicoes_senha").fetchall()
        self.assertEqual(len(linhas), 1)
        self.assertNotEqual(linhas[0][0], token)
        self.assertNotIn(token, linhas[0][0])
        self.assertEqual(linhas[0][0], auth._hash_token(token))

    def test_conta_inexistente_ou_desativada_devolve_none(self):
        self.assertIsNone(auth.pedir_redefinicao("ninguem@senha.test"))
        criar()
        c = db.conn(); c.execute("UPDATE usuarios SET ativo=0 WHERE email='ana@senha.test'"); c.commit()
        self.assertIsNone(auth.pedir_redefinicao("ana@senha.test"))

    def test_redefinir_troca_a_senha_e_a_antiga_para_de_funcionar(self):
        criar()
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        self.assertEqual(auth.redefinir_senha(token, "NovaSenha#1"), "ana@senha.test")
        self.assertIsNotNone(auth.autenticar("ana@senha.test", "NovaSenha#1"))
        self.assertIsNone(auth.autenticar("ana@senha.test", "senhaantiga1"))

    def test_token_e_de_uso_unico(self):
        criar()
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        auth.redefinir_senha(token, "NovaSenha#1")
        with self.assertRaises(ValueError):
            auth.redefinir_senha(token, "OutraSenha#2")

    def test_token_expira(self):
        criar()
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        c = db.conn()
        c.execute("UPDATE redefinicoes_senha SET expira=?", ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),))
        c.commit()
        with self.assertRaises(ValueError):
            auth.redefinir_senha(token, "NovaSenha#1")

    def test_token_invalido_ou_vazio(self):
        criar()
        for t in ("", "   ", "lixo", "a" * 43):
            with self.assertRaises(ValueError):
                auth.redefinir_senha(t, "NovaSenha#1")

    def test_senha_curta_e_recusada_e_o_token_continua_valido(self):
        criar()
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        with self.assertRaises(ValueError):
            auth.redefinir_senha(token, "123")
        auth.redefinir_senha(token, "SenhaOk#123")

    def test_novo_pedido_invalida_o_link_anterior(self):
        criar()
        _, _, antigo = auth.pedir_redefinicao("ana@senha.test")
        _, _, novo = auth.pedir_redefinicao("ana@senha.test")
        with self.assertRaises(ValueError):
            auth.redefinir_senha(antigo, "NovaSenha#1")
        auth.redefinir_senha(novo, "NovaSenha#1")

    def test_redefinir_encerra_todas_as_sessoes_da_conta(self):
        uid = criar()
        t1, t2 = auth.criar_sessao(uid), auth.criar_sessao(uid)
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        auth.redefinir_senha(token, "NovaSenha#1")
        self.assertIsNone(auth.usuario_da_sessao(t1))
        self.assertIsNone(auth.usuario_da_sessao(t2))

    def test_limite_de_pedidos_por_hora_na_mesma_conta(self):
        criar()
        for _ in range(auth.MAX_PEDIDOS_REDEFINICAO_HORA):
            self.assertIsNotNone(auth.pedir_redefinicao("ana@senha.test"))
        self.assertIsNone(auth.pedir_redefinicao("ana@senha.test"))


class TestRotas(unittest.TestCase):
    def setUp(self):
        limpar()

    def test_resposta_igual_com_ou_sem_conta_e_so_envia_quando_existe(self):
        criar()
        with mock.patch.object(emails, "enviar_redefinicao_em_segundo_plano") as env:
            r1 = app_mod.senha_esqueci(app_mod.EsqueciSenha(email="ana@senha.test"))
            r2 = app_mod.senha_esqueci(app_mod.EsqueciSenha(email="ninguem@senha.test"))
        self.assertEqual(r1, r2)
        self.assertEqual(env.call_count, 1)
        nome, email, token = env.call_args.args
        self.assertEqual((nome, email), ("Ana Souza", "ana@senha.test"))
        self.assertGreater(len(token), 30)

    def test_redefinir_pela_rota_e_erro_400_para_link_ruim(self):
        criar()
        _, _, token = auth.pedir_redefinicao("ana@senha.test")
        r = app_mod.senha_redefinir(app_mod.RedefinirSenha(token=token, senha="NovaSenha#1"))
        self.assertEqual(r, {"ok": True, "email": "ana@senha.test"})
        with self.assertRaises(HTTPException) as ctx:
            app_mod.senha_redefinir(app_mod.RedefinirSenha(token=token, senha="NovaSenha#1"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("não é mais válido", ctx.exception.detail)


class TestEmailRedefinicao(unittest.TestCase):
    def setUp(self):
        limpar()
        configuracoes.definir("email_remetente", "contato.thyfia@gmail.com")
        configuracoes.definir("email_senha_app", "abcdefghijklmnop")

    def test_mensagem_tem_link_botao_texto_e_imagens(self):
        msg = emails.montar_mensagem_redefinicao("Ana Souza", "ana@senha.test", "TOKEN123")
        self.assertEqual(msg["To"], "Ana Souza <ana@senha.test>")
        self.assertIn("nova senha", msg["Subject"].lower())
        link = "https://thyfia.integradata.app.br/?redefinir=TOKEN123"
        html = msg.get_body(preferencelist=("html",)).get_content()
        texto = msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn(f'href="{link}"', html)
        self.assertIn(link, texto)
        self.assertIn("Criar nova senha", html)
        self.assertIn("60 minutos", html)
        self.assertIn("Não foi você?", html)
        cids = re.findall(r'cid:([\w.]+)', html)
        partes = {p["Content-ID"].strip("<>") for p in msg.walk() if p["Content-ID"]}
        for cid in cids:
            self.assertIn(cid, partes)

    def test_link_usa_url_publica_configurada(self):
        configuracoes.definir("url_publica", "https://exemplo.test")
        self.assertEqual(emails.link_redefinicao("abc"), "https://exemplo.test/?redefinir=abc")

    def test_nome_com_html_e_escapado(self):
        html = emails.montar_html_redefinicao("<b>x</b>", "https://a.test/?redefinir=1")
        self.assertNotIn("<b>x</b>", html)

    def test_enviar_redefinicao_nao_levanta_sem_configuracao_nem_com_erro(self):
        configuracoes.definir("email_senha_app", "")
        self.assertFalse(emails.enviar_redefinicao("Ana", "ana@senha.test", "t"))
        configurar = lambda: configuracoes.definir("email_senha_app", "abcdefghijklmnop")
        configurar()
        with mock.patch.object(emails, "enviar", side_effect=ValueError("falhou")):
            self.assertFalse(emails.enviar_redefinicao("Ana", "ana@senha.test", "t"))
        with mock.patch.object(emails, "enviar") as env:
            self.assertTrue(emails.enviar_redefinicao("Ana", "ana@senha.test", "t"))
            self.assertEqual(env.call_count, 1)

    def test_teto_geral_de_emails_de_redefinicao_por_hora(self):
        with mock.patch.object(emails, "enviar"):
            for _ in range(emails.LIMITE_REDEFINICAO_POR_HORA):
                self.assertTrue(emails.enviar_redefinicao("Ana", "ana@senha.test", "t"))
            self.assertFalse(emails.enviar_redefinicao("Ana", "ana@senha.test", "t"))

    def test_o_limite_de_redefinicao_nao_consome_o_das_boas_vindas(self):
        with mock.patch.object(emails, "enviar"):
            for _ in range(emails.LIMITE_REDEFINICAO_POR_HORA):
                emails.enviar_redefinicao("Ana", "ana@senha.test", "t")
        self.assertEqual(len(emails._enviados), 0)


class TestFront(unittest.TestCase):
    def setUp(self):
        self.html = (ESTATICO / "index.html").read_text(encoding="utf-8")
        self.js = (ESTATICO / "app.js").read_text(encoding="utf-8")

    def test_link_e_telas_existem(self):
        for trecho in ('id="btn-esqueci"', 'id="form-esqueci"', 'id="form-redefinir"', 'id="esqueci-enviado"', "Esqueci minha senha"):
            self.assertIn(trecho, self.html)

    def test_js_chama_as_duas_rotas_e_le_o_token_da_url(self):
        for trecho in ("/api/senha/esqueci", "/api/senha/redefinir", 'get("redefinir")', "history.replaceState"):
            self.assertIn(trecho, self.js)

    def test_icone_de_email_existe_no_sprite(self):
        self.assertIn('id="i-mail"', self.html)

    def test_versoes_de_cache_foram_atualizadas(self):
        self.assertRegex(self.html, r"app\.js\?v=(19|[2-9]\d)")
        self.assertRegex(self.html, r"styles\.css\?v=(15|[2-9]\d)")


if __name__ == "__main__":
    unittest.main()
