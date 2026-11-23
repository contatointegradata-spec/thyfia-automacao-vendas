"""Planos, bloqueios por plano, webhook da Kiwify, configuração por cliente (multi-tenant) e admin."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException

from thyfia import auth, cofre, configuracoes, db, instagram, kiwify, planos, tenant
from thyfia import app as app_mod


def novo_usuario(nome="Cliente", email=None, papel="usuario"):
    email = email or f"c{datetime.now().timestamp()}@t.test".replace(".", "", 1)
    uid = auth.criar_usuario(nome, email, "senha123")
    c = db.conn()
    c.execute("UPDATE usuarios SET papel=? WHERE id=?", (papel, uid))
    c.commit()
    return c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()


def zerar():
    c = db.conn()
    for t in ("assinaturas", "eventos_pagamento", "configuracoes"):
        c.execute(f"DELETE FROM {t}")
    c.commit()


class Base(unittest.TestCase):
    def setUp(self):
        zerar()
        self.n = getattr(Base, "_n", 0) + 1
        Base._n = self.n
        self.u = novo_usuario(email=f"cli{self.n}@t.test")


class TestPlanos(Base):
    def test_sem_assinatura_nao_tem_nada(self):
        s = planos.situacao(self.u)
        self.assertFalse(s["ativo"])
        self.assertEqual(planos.liberados(self.u), {"gerar": False, "automacao": False, "agente": False})

    def test_cada_plano_libera_o_certo(self):
        esperado = {"plus": (True, False, False), "pro": (True, True, False), "premium": (True, True, True)}
        for plano, (g, a, ag) in esperado.items():
            planos.conceder(self.u["email"], plano, 31)
            lib = planos.liberados(self.u)
            self.assertEqual((lib["gerar"], lib["automacao"], lib["agente"]), (g, a, ag), plano)
            planos.marcar(self.u["email"], None, "reembolsada")

    def test_vale_o_maior_plano_e_cancelar_o_menor_nao_derruba(self):
        planos.conceder(self.u["email"], "plus", 31)
        planos.conceder(self.u["email"], "premium", 31)
        planos.marcar(self.u["email"], "plus", "reembolsada")
        self.assertEqual(planos.plano_do(self.u), "premium")

    def test_cancelada_vale_ate_o_fim_do_ciclo_e_reembolso_corta_na_hora(self):
        planos.conceder(self.u["email"], "pro", 10)
        planos.marcar(self.u["email"], "pro", "cancelada")
        self.assertEqual(planos.plano_do(self.u), "pro")
        planos.marcar(self.u["email"], "pro", "reembolsada")
        self.assertIsNone(planos.plano_do(self.u))

    def test_vencida_perde_acesso(self):
        planos.conceder(self.u["email"], "pro", valido_ate=(datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"))
        self.assertIsNone(planos.plano_do(self.u))

    def test_admin_tem_tudo(self):
        adm = novo_usuario(email="adm@t.test", papel="admin")
        self.assertTrue(all(planos.liberados(adm).values()))

    def test_email_e_comparado_sem_diferenciar_maiusculas(self):
        planos.conceder("  " + self.u["email"].upper(), "plus", 31)
        self.assertEqual(planos.plano_do(self.u), "plus")

    def test_exige_devolve_402_com_o_plano_necessario(self):
        dep = app_mod.exige("agente")
        planos.conceder(self.u["email"], "pro", 31)
        with self.assertRaises(HTTPException) as cm:
            dep(u=self.u)
        self.assertEqual(cm.exception.status_code, 402)
        self.assertIn("Premium", cm.exception.detail)
        planos.conceder(self.u["email"], "premium", 31)
        self.assertEqual(dep(u=self.u)["id"], self.u["id"])

    def test_precos(self):
        self.assertEqual([p["preco"] for p in planos.catalogo()], [19.90, 39.90, 69.90])


class TestRotasPorPlano(Base):
    def test_envio_exige_pro_e_whatsapp_cadastrado(self):
        planos.conceder(self.u["email"], "plus", 31)
        with self.assertRaises(HTTPException) as cm:  # 1º degrau da dependência: o plano
            app_mod.exige("automacao")(u=self.u)
        self.assertEqual(cm.exception.status_code, 402)
        planos.conceder(self.u["email"], "pro", 31)
        with self.assertRaises(HTTPException) as cm:  # 2º degrau: já informou onde divulgar?
            app_mod.exige_automacao_pronta(u=self.u)
        self.assertEqual(cm.exception.status_code, 409)
        tenant.salvar_canais(self.u["id"], "(34) 99999-1234", "")
        self.assertTrue(app_mod.exige_automacao_pronta(u=self.u))

    def test_envio_automatico_so_liga_com_plano_e_numero(self):
        with self.assertRaises(HTTPException) as cm:
            app_mod.salvar_preferencias(app_mod.Preferencias(auto_enviar=True), u=self.u)
        self.assertEqual(cm.exception.status_code, 402)
        planos.conceder(self.u["email"], "pro", 31)
        with self.assertRaises(HTTPException) as cm:
            app_mod.salvar_preferencias(app_mod.Preferencias(auto_enviar=True), u=self.u)
        self.assertEqual(cm.exception.status_code, 409)
        tenant.salvar_canais(self.u["id"], "34999991234", "")
        self.assertTrue(app_mod.salvar_preferencias(app_mod.Preferencias(auto_enviar=True), u=self.u)["auto_enviar"])
        # plano vencido: não envia sozinho mesmo com a opção ligada
        from thyfia import motor_ml
        self.assertTrue(motor_ml._quer_auto_envio(self.u["id"]))
        planos.marcar(self.u["email"], "pro", "reembolsada")
        self.assertFalse(motor_ml._quer_auto_envio(self.u["id"]))

    def test_rotas_reais_estao_protegidas(self):
        """Confere na tabela de rotas que cada recurso tem a dependência do plano certo."""
        def deps(path, metodo):
            r = next(r for r in app_mod.app.routes if getattr(r, "path", "") == path and metodo in r.methods)
            return [d.call for d in r.dependant.dependencies]
        nomes = lambda lst: {getattr(f, "__qualname__", "") for f in lst}
        for path, metodo in (("/api/jobs", "POST"), ("/api/ml/conectar", "POST")):
            self.assertTrue(any("exige" in n for n in nomes(deps(path, metodo))), path)
        for path, metodo in (("/api/publicacao/sugerir", "POST"), ("/api/instagram/status", "GET"),
                             ("/api/publicacao/publicar-agora", "POST"), ("/api/publicacao", "GET")):
            self.assertTrue(any("exige" in n for n in nomes(deps(path, metodo))), path)
        self.assertIn("exige_automacao_pronta", nomes(deps("/api/jobs/{job_id}/enviar", "POST")))
        self.assertIn("admin_atual", nomes(deps("/api/admin/usuarios", "GET")))


# --------------------------------------------------------------------------- Kiwify
TOKEN = "tok-kiwify-teste"


def corpo(evento="order_approved", status="paid", email="x@t.test", produto="PROD-PRO", extra=None):
    d = {"order_id": "ped1", "order_status": status, "webhook_event_type": evento,
         "Customer": {"full_name": "Fulano", "email": email}, "Product": {"product_id": produto, "product_name": "ThyfIA " + produto}}
    d.update(extra or {})
    return d


class TestKiwify(Base):
    def setUp(self):
        super().setUp()
        configuracoes.definir("kiwify_token", TOKEN)
        for p in planos.ORDEM:
            configuracoes.definir(f"kiwify_produto_{p}", f"PROD-{p.upper()}")

    def test_assinatura_hmac(self):
        b = json.dumps(corpo(), ensure_ascii=False).encode()
        sig = hmac.new(TOKEN.encode(), b, hashlib.sha1).hexdigest()
        self.assertTrue(kiwify.assinatura_confere(b, sig, TOKEN))
        self.assertTrue(kiwify.assinatura_confere(b, sig.upper(), TOKEN))
        self.assertFalse(kiwify.assinatura_confere(b, sig, "outro"))
        self.assertFalse(kiwify.assinatura_confere(b, None, TOKEN))
        self.assertFalse(kiwify.assinatura_confere(b + b" ", sig, TOKEN))
        # assinada sobre o JSON compacto, enviada com espaços
        compacto = json.dumps(corpo(), separators=(",", ":")).encode()
        bonito = json.dumps(corpo(), indent=2).encode()
        sig2 = hmac.new(TOKEN.encode(), compacto, hashlib.sha1).hexdigest()
        self.assertTrue(kiwify.assinatura_confere(bonito, sig2, TOKEN))

    def test_compra_aprovada_libera_o_plano_pelo_produto(self):
        r = kiwify.processar(corpo(email=self.u["email"], produto="PROD-PREMIUM"))
        self.assertEqual((r["resultado"], r["plano"]), ("aplicado", "premium"))
        self.assertEqual(planos.plano_do(self.u), "premium")

    def test_vale_com_email_ainda_sem_conta(self):
        kiwify.processar(corpo(email="futuro@t.test", produto="PROD-PRO"))
        uid = auth.criar_usuario("Futuro", "Futuro@T.test", "senha123")
        u = db.conn().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        self.assertEqual(planos.plano_do(u), "pro")

    def test_produto_pelo_nome_e_desconhecido(self):
        configuracoes.definir("kiwify_produto_plus", "ThyfIA Plus")
        r = kiwify.processar({**corpo(email=self.u["email"], produto="zzz"), "Product": {"product_id": "zzz", "product_name": "thyfia  plus"}})
        self.assertEqual(r["plano"], "plus")
        r = kiwify.processar(corpo(email=self.u["email"], produto="NAO-EXISTE"))
        self.assertEqual(r["resultado"], "erro")
        self.assertEqual(kiwify.eventos_recentes(1)[0]["resultado"], "erro")

    def test_ciclo_de_vida(self):
        e = self.u["email"]
        kiwify.processar(corpo(email=e, produto="PROD-PRO"))
        kiwify.processar(corpo("subscription_late", status="paid", email=e, produto="PROD-PRO"))
        self.assertEqual(planos.situacao(self.u)["status"], "atrasada")
        self.assertEqual(planos.plano_do(self.u), "pro")  # carência até o fim do ciclo pago
        kiwify.processar(corpo("subscription_renewed", email=e, produto="PROD-PRO"))
        self.assertEqual(planos.situacao(self.u)["status"], "ativa")
        kiwify.processar(corpo("subscription_canceled", status="paid", email=e, produto="PROD-PRO"))
        self.assertEqual(planos.situacao(self.u)["status"], "cancelada")
        self.assertEqual(planos.plano_do(self.u), "pro")
        kiwify.processar(corpo("order_refunded", status="refunded", email=e, produto="PROD-PRO"))
        self.assertIsNone(planos.plano_do(self.u))

    def test_chargeback_corta(self):
        kiwify.processar(corpo(email=self.u["email"], produto="PROD-PLUS"))
        kiwify.processar(corpo("chargeback", status="chargedback", email=self.u["email"], produto="PROD-PLUS"))
        self.assertIsNone(planos.plano_do(self.u))

    def test_eventos_sem_efeito_sao_ignorados(self):
        for ev, st in (("pix_created", "waiting_payment"), ("billet_created", "waiting_payment"), ("abandoned_cart", "")):
            self.assertEqual(kiwify.processar(corpo(ev, status=st, email=self.u["email"]))["resultado"], "ignorado")
        self.assertIsNone(planos.plano_do(self.u))

    def test_proxima_cobranca_define_a_validade(self):
        prox = (datetime.now() + timedelta(days=20)).isoformat()
        kiwify.processar(corpo(email=self.u["email"], produto="PROD-PRO", extra={"Subscription": {"id": "s1", "next_payment": prox}}))
        ate = datetime.fromisoformat(planos.situacao(self.u)["valido_ate"])
        self.assertAlmostEqual((ate - datetime.now()).days, 22, delta=1)

    def test_payload_estranho_nao_derruba(self):
        for lixo in ({}, {"Customer": None}, {"order_status": "paid"}, [], "x"):
            kiwify.processar(lixo)  # não levanta

    def test_endpoint_exige_assinatura(self):
        import asyncio

        class Req:
            def __init__(s, b, q):
                s._b, s.query_params = b, q
            async def body(s):
                return s._b

        b = json.dumps(corpo(email=self.u["email"], produto="PROD-PRO")).encode()
        sig = hmac.new(TOKEN.encode(), b, hashlib.sha1).hexdigest()
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(app_mod.kiwify_webhook(Req(b, {})))
        self.assertEqual(cm.exception.status_code, 401)
        with self.assertRaises(HTTPException):
            asyncio.run(app_mod.kiwify_webhook(Req(b, {"signature": "0" * 40})))
        self.assertIsNone(planos.plano_do(self.u))
        self.assertEqual(kiwify.eventos_recentes(1)[0]["resultado"], "recusado")
        r = asyncio.run(app_mod.kiwify_webhook(Req(b, {"signature": sig})))
        self.assertEqual(r["resultado"], "aplicado")
        self.assertEqual(planos.plano_do(self.u), "pro")
        # token na URL também vale; sem token configurado → 503
        self.assertEqual(asyncio.run(app_mod.kiwify_webhook(Req(b, {"token": TOKEN})))["resultado"], "aplicado")
        configuracoes.definir("kiwify_token", "")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(app_mod.kiwify_webhook(Req(b, {"signature": sig})))
        self.assertEqual(cm.exception.status_code, 503)


# --------------------------------------------------------------------------- configuração do cliente
class TestTenant(Base):
    def test_numero(self):
        self.assertEqual(tenant.normalizar_numero("(34) 99999-1234"), "5534999991234")
        self.assertEqual(tenant.normalizar_numero("+55 34 3822-1234"), "553438221234")
        self.assertEqual(tenant.normalizar_numero("5534999991234"), "5534999991234")
        for ruim in ("", "123", "abc", "(34) 9999-123456789012"):
            with self.assertRaises(tenant.ErroConfig):
                tenant.normalizar_numero(ruim)
        self.assertEqual(tenant.formatar_numero("5534999991234"), "+55 (34) 99999-1234")

    def test_canal_telegram(self):
        self.assertEqual(tenant.normalizar_canal("minhasofertas"), "@minhasofertas")
        self.assertEqual(tenant.normalizar_canal("https://t.me/minhasofertas"), "@minhasofertas")
        self.assertEqual(tenant.normalizar_canal("-1001234567890"), "-1001234567890")
        self.assertEqual(tenant.normalizar_canal(""), "")
        with self.assertRaises(tenant.ErroConfig):
            tenant.normalizar_canal("@a b")

    def test_cada_cliente_tem_o_seu_destino_no_envio(self):
        outro = novo_usuario(email=f"outro{self.n}@t.test")
        tenant.salvar_canais(self.u["id"], "34999991111", "@canal_um")
        tenant.salvar_canais(outro["id"], "11988882222", "")
        a, b = tenant.cliente_payload(self.u["id"]), tenant.cliente_payload(outro["id"])
        self.assertEqual((a["whatsapp"], a["telegram"]), ("5534999991111", "@canal_um"))
        self.assertEqual((b["whatsapp"], b["telegram"]), ("5511988882222", ""))

    def test_um_produto_com_tres_planos_casa_pelo_nome_do_plano(self):
        for p, nome in (("plus", "ThyfIA Plus"), ("pro", "ThyfIA Pro"), ("premium", "ThyfIA Premium")):
            configuracoes.definir(f"kiwify_produto_{p}", nome)
        base = {"webhook_event_type": "order_approved", "order_status": "paid", "order_id": "X1",
                "Customer": {"email": self.u["email"], "full_name": "C"},
                "Product": {"product_id": "uuid-do-produto", "product_name": "ThyfIA"}}
        for nome, esperado in (("ThyfIA Pro", "pro"), ("ThyfIA Premium", "premium"), ("ThyfIA Plus", "plus")):
            r = kiwify.processar({**base, "Subscription": {"id": "S1", "plan": {"id": "p", "name": nome}}})
            self.assertEqual((r["resultado"], r.get("plano")), ("aplicado", esperado))
        r = kiwify.processar({**base, "Subscription": {"id": "S1", "plan": {"id": "p", "name": "Outro"}}})
        self.assertEqual(r["resultado"], "erro")  # plano desconhecido: não libera nada

    def test_cancelamento_apos_reembolso_nao_desfaz_o_reembolso(self):
        planos.conceder(self.u["email"], "pro", origem="kiwify", valido_ate="2099-01-01T00:00:00")
        planos.marcar(self.u["email"], "pro", "reembolsada")
        planos.marcar(self.u["email"], "pro", "cancelada")
        s = planos.situacao(self.u)
        self.assertFalse(s["ativo"])
        self.assertEqual(s["status"], "reembolsada")

    def test_dono_sem_numero_mantem_o_envio_como_antes(self):
        dono = novo_usuario(email=f"dono{self.n}@t.test", papel="admin")
        self.assertTrue(tenant.automacao_pronta(dono["id"]))   # usa o destino padrão do n8n
        self.assertEqual(tenant.cliente_payload(dono["id"]), {})  # sem "cliente": n8n legado
        self.assertFalse(tenant.automacao_pronta(self.u["id"]))   # cliente comum precisa do número
        tenant.salvar_canais(dono["id"], "34999990000", "")
        self.assertEqual(tenant.cliente_payload(dono["id"])["whatsapp"], "5534999990000")

    def test_instagram_token_cifrado_e_validado_antes_de_salvar(self):
        token = "EAAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-SEGREDO"
        with mock.patch.object(instagram, "validar_conta", side_effect=instagram.ErroInstagram("token inválido", "token")):
            with self.assertRaises(tenant.ErroConfig):
                tenant.instagram_salvar(self.u["id"], "17841450161230918", token)
        self.assertFalse(tenant.publica(self.u["id"])["instagram"]["configurado"])
        vistas = []
        with mock.patch.object(instagram, "validar_conta", lambda: vistas.append(instagram.config()["token"]) or {"username": "loja"}):
            r = tenant.instagram_salvar(self.u["id"], "17841450161230918", token)
        self.assertEqual(vistas, [token])
        self.assertEqual(r["username"], "loja")
        bruto = db.conn().execute("SELECT ig_token_cifrado FROM config_usuario WHERE usuario_id=?", (self.u["id"],)).fetchone()[0]
        self.assertNotIn("SEGREDO", bruto)
        self.assertEqual(cofre.abrir(bruto), token)
        self.assertNotIn("SEGREDO", json.dumps(tenant.publica(self.u["id"])))  # nunca volta inteiro
        with self.assertRaises(tenant.ErroConfig):
            tenant.instagram_salvar(self.u["id"], "abc", token)
        with self.assertRaises(tenant.ErroConfig):
            tenant.instagram_salvar(self.u["id"], "17841450161230918", "curto")

    def test_instagram_nunca_usa_a_conta_de_outro_cliente(self):
        a, b = novo_usuario(email=f"ia{self.n}@t.test"), novo_usuario(email=f"ib{self.n}@t.test")
        with mock.patch.object(instagram, "validar_conta", return_value={"username": "a"}):
            tenant.instagram_salvar(a["id"], "17841400000000001", "TOKEN-A-" + "x" * 40)
        with mock.patch.dict("os.environ", {"THYFIA_IG_ENV_FALLBACK": "0"}):
            with tenant.ig_ctx(a["id"]):
                self.assertEqual(instagram.config()["user_id"], "17841400000000001")
            with tenant.ig_ctx(b["id"]):  # sem conta própria e não é admin → "não configurado"
                self.assertFalse(instagram.configurado())
            adm = novo_usuario(email=f"adm{self.n}@t.test", papel="admin")
            with tenant.ig_ctx(adm["id"]):  # o dono pode usar o .env
                self.assertTrue(instagram.configurado())

    def test_remover_instagram(self):
        with mock.patch.object(instagram, "validar_conta", return_value={"username": "a"}):
            tenant.instagram_salvar(self.u["id"], "17841400000000001", "TOKEN-A-" + "x" * 40)
        self.assertFalse(tenant.instagram_remover(self.u["id"])["configurado"])

    def test_checklist_acompanha_o_plano(self):
        self.assertEqual(tenant.checklist(self.u), [])
        planos.conceder(self.u["email"], "plus", 31)
        self.assertEqual([p["id"] for p in tenant.checklist(self.u)], ["ml", "shopee", "gerar"])
        planos.conceder(self.u["email"], "premium", 31)
        ids = [p["id"] for p in tenant.checklist(self.u)]
        self.assertEqual(ids, ["ml", "shopee", "gerar", "whatsapp", "telegram", "envio", "meta", "post"])

    def test_teste_de_envio_mensagens(self):
        tenant.salvar_canais(self.u["id"], "34999991234", "@canal_teste")
        with self.assertRaises(tenant.ErroConfig) as cm:
            tenant.testar_whatsapp(self.u["id"])
        self.assertEqual(cm.exception.status, 503)
        for k, v in (("evolution_url", "https://evo.test"), ("evolution_apikey", "K"), ("evolution_instancia", "Minha Inst")):
            configuracoes.definir(k, v)
        with mock.patch.object(tenant.requests, "post", return_value=mock.Mock(status_code=201)) as post:
            tenant.testar_whatsapp(self.u["id"])
        self.assertEqual(post.call_args[0][0], "https://evo.test/message/sendText/Minha%20Inst")
        self.assertEqual(json.loads(post.call_args[1]["data"])["number"], "5534999991234")
        configuracoes.definir("telegram_bot_token", "123:abc")
        with mock.patch.object(tenant.requests, "post", return_value=mock.Mock(status_code=400)):
            with self.assertRaises(tenant.ErroConfig) as cm:
                tenant.testar_telegram(self.u["id"])
        self.assertIn("ADMINISTRADOR", str(cm.exception))
        self.assertNotIn("123:abc", str(cm.exception))


class TestAutomacaoPorCliente(Base):
    def test_config_vem_da_tela_e_cai_no_env(self):
        from thyfia import automacao
        self.assertTrue(automacao.configurada())  # .env dos testes
        configuracoes.definir("n8n_webhook_url", "https://n8n.novo.test/hook")
        configuracoes.definir("n8n_api_key", "nova-chave")
        self.assertEqual(automacao.config(), ("https://n8n.novo.test/hook", "nova-chave"))
        self.assertNotIn("nova-chave", db.conn().execute("SELECT valor FROM configuracoes WHERE chave='n8n_api_key'").fetchone()[0])

    def test_payload_leva_o_cliente_so_quando_informado(self):
        from thyfia import automacao
        l = [{"titulo": "A", "preco": "1,00", "url": "u", "link": "https://meli.la/a", "busca": "k"}]
        self.assertNotIn("cliente", automacao.montar_payloads("k", l)[0])
        self.assertEqual(automacao.montar_payloads("k", l, {"id": 7})[0]["cliente"], {"id": 7})


# --------------------------------------------------------------------------- admin
class TestAdmin(Base):
    def setUp(self):
        super().setUp()
        self.adm = novo_usuario(email=f"adm{self.n}@t.test", papel="admin")

    def test_so_admin(self):
        with self.assertRaises(HTTPException) as cm:
            app_mod.admin_atual(u=self.u)
        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(app_mod.admin_atual(u=self.adm)["id"], self.adm["id"])

    def test_conceder_listar_e_revogar(self):
        app_mod.admin_conceder(app_mod.Concessao(email=self.u["email"], plano="pro", dias=10), u=self.adm)
        lista = app_mod.admin_usuarios(busca=self.u["email"], u=self.adm)
        self.assertEqual((lista[0]["plano"], lista[0]["origem"]), ("pro", "manual"))
        with self.assertRaises(HTTPException):
            app_mod.admin_conceder(app_mod.Concessao(email="ruim", plano="pro"), u=self.adm)
        with self.assertRaises(HTTPException):
            app_mod.admin_conceder(app_mod.Concessao(email=self.u["email"], plano="gold"), u=self.adm)
        app_mod.admin_revogar(email=self.u["email"], plano="pro", u=self.adm)
        self.assertIsNone(planos.plano_do(self.u))

    def test_resumo_receita(self):
        planos.conceder("a@t.test", "plus", 31)
        planos.conceder("b@t.test", "premium", 31)
        r = app_mod.admin_resumo(u=self.adm)
        self.assertEqual(r["assinantes"], {"plus": 1, "pro": 0, "premium": 1})
        self.assertEqual(r["receita_mensal"], 89.80)

    def test_desativar_derruba_a_sessao_e_nao_pode_a_si_mesmo(self):
        tok = auth.criar_sessao(self.u["id"])
        self.assertIsNotNone(auth.usuario_da_sessao(tok))
        app_mod.admin_editar_usuario(self.u["id"], app_mod.EstadoConta(ativo=False), u=self.adm)
        self.assertIsNone(auth.usuario_da_sessao(tok))
        self.assertIsNone(auth.autenticar(self.u["email"], "senha123") and auth.usuario_da_sessao(auth.criar_sessao(self.u["id"])))
        with self.assertRaises(HTTPException):
            app_mod.admin_editar_usuario(self.adm["id"], app_mod.EstadoConta(admin=False), u=self.adm)

    def test_config_segredos_cifrados_e_mascarados(self):
        app_mod.admin_config_salvar(app_mod.NovaConfig(valores={"kiwify_token": "segredo-longo-1234", "kiwify_link_pro": "https://pay.kiwify.com.br/abc"}),
                                    request=mock.Mock(base_url="https://x.test/"), u=self.adm)
        cfg = {c["chave"]: c for c in app_mod.admin_config(mock.Mock(base_url="https://x.test/"), u=self.adm)["campos"]}
        self.assertTrue(cfg["kiwify_token"]["definido"])
        self.assertEqual(cfg["kiwify_token"]["valor"], "")
        self.assertNotIn("segredo-longo", json.dumps(cfg))
        self.assertEqual(cfg["kiwify_link_pro"]["valor"], "https://pay.kiwify.com.br/abc")
        self.assertEqual(configuracoes.obter("kiwify_token"), "segredo-longo-1234")
        # segredo em branco mantém o atual
        app_mod.admin_config_salvar(app_mod.NovaConfig(valores={"kiwify_token": ""}), request=mock.Mock(base_url="https://x.test/"), u=self.adm)
        self.assertEqual(configuracoes.obter("kiwify_token"), "segredo-longo-1234")
        with self.assertRaises(HTTPException):
            app_mod.admin_config_salvar(app_mod.NovaConfig(valores={"hack": "1"}), request=mock.Mock(base_url="https://x.test/"), u=self.adm)

    def test_simulador_usa_o_mesmo_caminho_do_webhook(self):
        configuracoes.definir("kiwify_produto_pro", "ABC123")
        r = app_mod.admin_kiwify_simular(app_mod.Simulacao(email=self.u["email"], produto="ABC123"), u=self.adm)
        self.assertEqual(r["plano"], "pro")
        self.assertEqual(planos.plano_do(self.u), "pro")

    def test_primeiro_usuario_vira_admin_quando_nao_ha_admin(self):
        c = db.conn()
        c.execute("UPDATE usuarios SET papel='usuario'")
        c.commit()
        auth.garantir_admin()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM usuarios WHERE papel='admin'").fetchone()[0], 1)
        with mock.patch.dict("os.environ", {"THYFIA_ADMIN_EMAIL": self.u["email"]}):
            auth.garantir_admin()
        self.assertEqual(c.execute("SELECT papel FROM usuarios WHERE id=?", (self.u["id"],)).fetchone()[0], "admin")


class TestAdminPorEnv(Base):
    def test_senha_do_env_cria_e_redefine_o_admin_so_na_subida(self):
        email = f"dono-env{self.n}@t.test"
        env = {"THYFIA_ADMIN_EMAIL": email, "THYFIA_ADMIN_SENHA": "SenhaForte#1"}
        with mock.patch.dict("os.environ", env):
            auth.garantir_admin(inicial=True)            # cria a conta
            u = auth.autenticar(email, "SenhaForte#1")
            self.assertIsNotNone(u)
            self.assertEqual(u["papel"], "admin")
            db.conn().execute("UPDATE usuarios SET ativo=0 WHERE email=?", (email,))
            db.conn().commit()
            auth.garantir_admin()                         # cadastro comum não mexe na senha/ativo
            self.assertEqual(db.conn().execute("SELECT ativo FROM usuarios WHERE email=?", (email,)).fetchone()[0], 0)
            os.environ["THYFIA_ADMIN_SENHA"] = "OutraSenha#2"
            auth.garantir_admin(inicial=True)             # redefine e reativa
            self.assertIsNone(auth.autenticar(email, "SenhaForte#1"))
            u = auth.autenticar(email, "OutraSenha#2")
            self.assertEqual((u["ativo"], u["papel"]), (1, "admin"))

    def test_senha_curta_no_env_e_ignorada(self):
        email = f"dono-curto{self.n}@t.test"
        with mock.patch.dict("os.environ", {"THYFIA_ADMIN_EMAIL": email, "THYFIA_ADMIN_SENHA": "123"}):
            auth.garantir_admin(inicial=True)
        self.assertIsNone(db.conn().execute("SELECT 1 FROM usuarios WHERE email=?", (email,)).fetchone())


class TestPerfilEPlanos(Base):
    def test_eu_traz_plano_e_liberados_e_planos_traz_checkout_com_email(self):
        configuracoes.definir("kiwify_link_pro", "https://pay.kiwify.com.br/abc")
        planos.conceder(self.u["email"], "pro", 31)
        eu = app_mod._perfil_usuario(self.u)
        self.assertEqual(eu["plano"]["plano"], "pro")
        self.assertEqual(eu["liberados"], {"gerar": True, "automacao": True, "agente": False})
        cat = app_mod.listar_planos(u=self.u)
        pro = next(p for p in cat["planos"] if p["id"] == "pro")
        self.assertTrue(pro["atual"])
        self.assertIn("email=", pro["checkout"])
        self.assertTrue(pro["checkout"].startswith("https://pay.kiwify.com.br/abc?"))
        self.assertEqual(next(p for p in cat["planos"] if p["id"] == "premium")["checkout"], "")


if __name__ == "__main__":
    unittest.main()
