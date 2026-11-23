"""Shopee Afiliados (Open API): assinatura, consulta, leitura dos produtos, credenciais cifradas, busca com
fallback, links curtos em lotes, geração do job, rotas e as telas do front."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from thyfia import auth, cerebro, db, itens, shopee
from thyfia import app as app_mod

ESTATICO = Path(__file__).resolve().parents[1] / "thyfia" / "static"
APP_ID, SECRET = "123456789", "SEGREDO_DE_TESTE_1234567890abcdef"


def no(item=1, nome="Capinha iPhone 13 Silicone", preco="29.9", vendas=500, nota=4.8, taxa="0.1", shop=77):
    return {"itemId": str(item), "productName": nome, "productLink": f"https://shopee.com.br/x-i.{shop}.{item}?sp=1",
            "offerLink": f"https://s.shopee.com.br/af{item}", "imageUrl": f"https://down-br.img.susercontent.com/file/img{item}",
            "priceMin": preco, "priceMax": preco, "sales": vendas, "ratingStar": str(nota), "commissionRate": taxa,
            "shopId": str(shop), "shopName": "Loja"}


def resposta(dados=None, codigo=None, texto="", http=200):
    r = mock.Mock()
    r.ok = http < 400
    r.status_code = http
    r.json.return_value = ({"errors": [{"message": texto, "extensions": {"code": codigo, "message": texto}}]}
                           if codigo else {"data": dados or {}})
    return r


def limpar():
    c = db.conn()
    for t in ("job_itens", "jobs", "shopee_conexoes", "produtos"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM usuarios WHERE email LIKE '%@shopee.test'")
    c.commit()


def usuario(email="lia@shopee.test"):
    uid = auth.criar_usuario("Lia Prado", email, "senha-segura1")
    return uid


class Base(unittest.TestCase):
    def setUp(self):
        limpar()
        self.uid = usuario()
        p = mock.patch.object(shopee, "_dormir", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def conectar(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta({"productOfferV2": {"nodes": []}})):
            shopee.conectar(self.uid, APP_ID, SECRET)


class TestAssinatura(Base):
    def test_assinatura_e_sha256_de_appid_ts_corpo_secret(self):
        ass = shopee._assinar(APP_ID, SECRET, '{"query":"x"}', 1700000000)
        self.assertEqual(ass, hashlib.sha256(f'{APP_ID}1700000000{{"query":"x"}}{SECRET}'.encode()).hexdigest())

    def test_cabecalho_e_corpo_enviados_conferem_com_a_assinatura(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta({"ok": 1})) as post:
            shopee._chamar(APP_ID, SECRET, "{ a }")
        corpo = post.call_args.kwargs["data"].decode()
        cab = post.call_args.kwargs["headers"]["Authorization"]
        self.assertEqual(json.loads(corpo), {"query": "{ a }"})
        ts = int(cab.split("Timestamp=")[1].split(",")[0])
        self.assertTrue(cab.startswith(f"SHA256 Credential={APP_ID}, Timestamp="))
        self.assertTrue(cab.endswith(f"Signature={shopee._assinar(APP_ID, SECRET, corpo, ts)}"))
        self.assertNotIn(SECRET, cab)

    def test_corpo_e_ascii_mesmo_com_acentos(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta({})) as post:
            shopee._chamar(APP_ID, SECRET, shopee.consulta_busca("pão de açúcar", 5, 1, "itemId"))
        post.call_args.kwargs["data"].decode("ascii")  # não levanta


class TestConsulta(unittest.TestCase):
    def test_busca_escapa_aspas_e_limita_o_termo(self):
        q = shopee.consulta_busca('fone "bluetooth" \\ x', 10, 1, "itemId")
        self.assertIn('keyword: "fone bluetooth x"', q)
        self.assertIn("limit: 10", q)
        self.assertLessEqual(len(shopee.limpar_termo("a" * 300)), 80)

    def test_links_em_lote_usam_alias_e_sub_ids(self):
        q = shopee.consulta_links(["https://shopee.com.br/a", "https://shopee.com.br/b"], ["thyfia", "capinha"])
        self.assertTrue(q.startswith("mutation {"))
        self.assertIn("l0: generateShortLink", q)
        self.assertIn("l1: generateShortLink", q)
        self.assertIn('subIds: ["thyfia", "capinha"]', q)

    def test_sub_ids_so_aceitam_caracteres_seguros_e_ate_5(self):
        self.assertEqual(shopee._sub_ids("thyfia", 'ca"pi nha', "", "a", "b", "c", "d"), ["thyfia", "capinha", "a", "b"])


class TestProdutos(unittest.TestCase):
    def test_no_valido_vira_produto(self):
        p = shopee.produto_de_no(no())
        self.assertEqual(p["url"], "https://shopee.com.br/product/77/1")
        self.assertEqual((p["preco"], p["vendas"], p["nota"], p["comissao"]), (29.9, 500, 4.8, 0.1))
        self.assertEqual(p["link"], "https://s.shopee.com.br/af1")

    def test_sem_titulo_preco_ou_link_e_descartado(self):
        for campo in ("productName", "priceMin", "offerLink"):
            n = no(); n[campo] = ""
            self.assertIsNone(shopee.produto_de_no(n), campo)

    def test_comissao_em_percentual_vira_fracao(self):
        self.assertAlmostEqual(shopee._taxa("12"), 0.12)
        self.assertAlmostEqual(shopee._taxa("0.12"), 0.12)
        self.assertEqual(shopee._taxa(None), 0.0)

    def test_nota_fora_de_faixa_e_ignorada(self):
        self.assertIsNone(shopee.produto_de_no(no(nota=9))["nota"])

    def test_ranking_tira_repetidos_e_fora_do_assunto_e_prioriza_vendas(self):
        ps = [shopee.produto_de_no(n) for n in (
            no(1, "Capinha iPhone 13 Silicone", vendas=10),
            no(2, "Capinha iPhone 13 Transparente", vendas=5000),
            no(2, "Capinha iPhone 13 Transparente", vendas=5000),
            no(3, "Tapete de banheiro", vendas=99999),
        )]
        r = shopee.ranquear(ps, "capinha iphone")
        self.assertEqual([p["url"].rsplit("/", 1)[1] for p in r], ["2", "1"])


class TestCredenciais(Base):
    def test_conectar_testa_com_consulta_real_e_cifra_o_secret(self):
        self.conectar()
        linha = db.conn().execute("SELECT * FROM shopee_conexoes WHERE usuario_id=?", (self.uid,)).fetchone()
        self.assertNotIn(SECRET, linha["secret_cifrado"])
        self.assertEqual(shopee.credenciais(self.uid), (APP_ID, SECRET))
        st = shopee.status(self.uid)
        self.assertEqual(st["status"], "conectado")
        self.assertNotIn(SECRET, json.dumps(st))

    def test_formato_invalido_nem_chama_a_shopee(self):
        with mock.patch.object(shopee.requests, "post") as post:
            for app_id, secret in (("abc", SECRET), (APP_ID, "curto")):
                with self.assertRaises(ValueError):
                    shopee.conectar(self.uid, app_id, secret)
        post.assert_not_called()

    def test_credencial_recusada_nao_e_gravada_e_a_mensagem_e_clara(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta(codigo=10020, texto="Invalid Signature")):
            with self.assertRaises(ValueError) as cm:
                shopee.conectar(self.uid, APP_ID, SECRET)
        self.assertIn("App ID e o Secret", str(cm.exception))
        self.assertEqual(shopee.status(self.uid)["status"], "desconectado")

    def test_sem_acesso_a_open_api_explica_o_que_fazer(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta(codigo=10035)):
            with self.assertRaises(ValueError) as cm:
                shopee.conectar(self.uid, APP_ID, SECRET)
        self.assertIn("Open API", str(cm.exception))

    def test_desconectar_apaga(self):
        self.conectar()
        self.assertEqual(shopee.desconectar(self.uid)["status"], "desconectado")
        self.assertIsNone(shopee.credenciais(self.uid))


class TestChamada(Base):
    def test_limite_de_consultas_repete_e_depois_funciona(self):
        seq = [resposta(codigo=10030), resposta(codigo=10030), resposta({"ok": 1})]
        with mock.patch.object(shopee.requests, "post", side_effect=seq) as post:
            self.assertEqual(shopee._chamar(APP_ID, SECRET, "{ a }"), {"ok": 1})
        self.assertEqual(post.call_count, 3)

    def test_limite_persistente_desiste_com_mensagem(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta(codigo=10030)):
            with self.assertRaises(shopee.ErroShopee) as cm:
                shopee._chamar(APP_ID, SECRET, "{ a }")
        self.assertEqual(cm.exception.codigo, 10030)

    def test_falha_de_rede_vira_erro_amigavel(self):
        with mock.patch.object(shopee.requests, "post", side_effect=shopee.requests.ConnectionError("x")):
            with self.assertRaises(shopee.ErroShopee) as cm:
                shopee._chamar(APP_ID, SECRET, "{ a }")
        self.assertIn("Tente de novo", str(cm.exception))

    def test_codigo_desconhecido_mostra_o_texto_da_shopee(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta(codigo=99999, texto="algo novo")):
            with self.assertRaises(shopee.ErroShopee) as cm:
                shopee._chamar(APP_ID, SECRET, "{ a }")
        self.assertIn("algo novo", str(cm.exception))


class TestBuscaELinks(Base):
    def setUp(self):
        super().setUp()
        self.conectar()

    def test_busca_devolve_ranqueado_e_respeita_o_limite(self):
        nos = [no(i, f"Capinha iPhone {i}", vendas=i * 10) for i in range(1, 9)]
        with mock.patch.object(shopee.requests, "post", return_value=resposta({"productOfferV2": {"nodes": nos}})):
            r = shopee.buscar(self.uid, "capinha iphone", 3)
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0]["vendas"], 80)

    def test_se_a_shopee_recusa_os_campos_completos_tenta_os_basicos(self):
        seq = [resposta(codigo=11001, texto="Cannot query field"),
               resposta({"productOfferV2": {"nodes": [no(1)]}})]
        with mock.patch.object(shopee.requests, "post", side_effect=seq) as post:
            r = shopee.buscar(self.uid, "capinha", 5)
        self.assertEqual(len(r), 1)
        self.assertNotIn("commissionRate", json.loads(post.call_args.kwargs["data"])["query"])

    def test_credencial_que_caiu_marca_a_conexao_com_erro(self):
        with mock.patch.object(shopee.requests, "post", return_value=resposta(codigo=10020)):
            with self.assertRaises(shopee.ErroShopee):
                shopee.buscar(self.uid, "capinha", 5)
        self.assertEqual(shopee.status(self.uid)["status"], "erro")

    def test_sem_conexao_pede_para_conectar(self):
        shopee.desconectar(self.uid)
        with self.assertRaises(shopee.ErroShopee) as cm:
            shopee.buscar(self.uid, "capinha", 5)
        self.assertIn("Conecte", str(cm.exception))

    def test_links_curtos_em_lotes_e_lote_que_falha_nao_derruba_os_outros(self):
        urls = [f"https://shopee.com.br/product/1/{i}" for i in range(shopee.LOTE_LINKS + 2)]
        lote1 = {f"l{k}": {"shortLink": f"https://s.shopee.com.br/c{k}"} for k in range(shopee.LOTE_LINKS)}
        seq = [resposta(lote1)] + [resposta(codigo=99999, texto="falhou")]
        with mock.patch.object(shopee.requests, "post", side_effect=seq):
            r = shopee.links_curtos(self.uid, urls, ["thyfia"])
        self.assertEqual(len(r), shopee.LOTE_LINKS)
        self.assertEqual(r[urls[0]], "https://s.shopee.com.br/c0")

    def test_gerar_link_so_aceita_enderecos_da_shopee(self):
        for ruim in ("https://mercadolivre.com.br/x", "javascript:alert(1)", "https://shopee.com.br.golpe.com/x", "abc"):
            with self.assertRaises(ValueError, msg=ruim):
                shopee.gerar_link(self.uid, ruim)

    def test_gerar_link_devolve_o_link_curto(self):
        url = "https://shopee.com.br/product/77/1"
        with mock.patch.object(shopee.requests, "post", return_value=resposta({"l0": {"shortLink": "https://s.shopee.com.br/abc"}})) as post:
            self.assertEqual(shopee.gerar_link(self.uid, url), "https://s.shopee.com.br/abc")
        self.assertIn('subIds: ["thyfia"]', json.loads(post.call_args.kwargs["data"])["query"])


class TestJob(Base):
    def setUp(self):
        super().setUp()
        self.conectar()
        self.job = "job-shopee-1"
        c = db.conn()
        c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, fonte) VALUES (?,?,?,?,?)",
                  (self.job, self.uid, "capinha iphone", 3, "shopee"))
        c.commit()

    def rodar(self, nos, curtos):
        seq = [resposta({"productOfferV2": {"nodes": nos}}), resposta(curtos)]
        with mock.patch.object(shopee.requests, "post", side_effect=seq):
            shopee.executar_job(self.job, self.uid, "capinha iphone", 3)
        return db.conn().execute("SELECT * FROM jobs WHERE id=?", (self.job,)).fetchone()

    def test_gera_itens_com_link_curto_e_conclui(self):
        nos = [no(i, f"Capinha iPhone {i}", vendas=i * 100) for i in (1, 2, 3)]
        curtos = {f"l{k}": {"shortLink": f"https://s.shopee.com.br/c{k}"} for k in range(3)}
        j = self.rodar(nos, curtos)
        self.assertEqual((j["status"], j["total"], j["ok"]), ("concluido", 3, 3))
        linhas = db.conn().execute("SELECT * FROM job_itens WHERE job_id=?", (self.job,)).fetchall()
        self.assertEqual(len(linhas), 3)
        self.assertTrue(all(r["link_afiliado"].startswith("https://s.shopee.com.br/c") for r in linhas))
        self.assertTrue(all(r["url_produto"].startswith("https://shopee.com.br/product/") for r in linhas))
        self.assertTrue(all(r["imagem"] for r in linhas))

    def test_sem_link_curto_vale_o_link_da_propria_busca(self):
        j = self.rodar([no(1, "Capinha iPhone 1")], {})
        self.assertEqual(j["status"], "concluido")
        r = db.conn().execute("SELECT link_afiliado FROM job_itens WHERE job_id=?", (self.job,)).fetchone()
        self.assertEqual(r["link_afiliado"], "https://s.shopee.com.br/af1")

    def test_busca_sem_resultado_termina_com_erro_claro(self):
        j = self.rodar([], {})
        self.assertEqual(j["status"], "erro")
        self.assertIn("não achou produtos", j["erro"])

    def test_aprende_no_cerebro_so_para_a_shopee(self):
        self.rodar([no(1, "Capinha iPhone 1")], {})
        self.assertTrue(cerebro.recuperar("capinha iphone", 5, loja="shopee.com.br"))
        self.assertEqual(cerebro.recuperar("capinha iphone", 5), [])  # o Mercado Livre não recebe produto da Shopee


class TestRotas(Base):
    def u(self):
        return {"id": self.uid, "plano": "pro", "nome": "Lia"}

    def test_criar_job_shopee_exige_conexao(self):
        with self.assertRaises(HTTPException) as cm:
            app_mod.criar_job(app_mod.NovoJob(termo="capinha", fonte="shopee"), self.u())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("Shopee", cm.exception.detail)

    def test_criar_job_shopee_grava_a_fonte_e_dispara_o_motor_da_shopee(self):
        self.conectar()
        with mock.patch.object(app_mod.threading, "Thread") as th:
            r = app_mod.criar_job(app_mod.NovoJob(termo="capinha iphone", fonte="shopee"), self.u())
        self.assertEqual(r["modo"], "shopee")
        self.assertIs(th.call_args.kwargs["target"], shopee.executar_job)
        linha = db.conn().execute("SELECT fonte FROM jobs WHERE id=?", (r["id"],)).fetchone()
        self.assertEqual(linha["fonte"], "shopee")

    def test_fonte_invalida_e_rejeitada(self):
        with self.assertRaises(Exception):
            app_mod.NovoJob(termo="capinha", fonte="amazon")

    def test_rota_de_link_sem_conexao_e_conectar_com_credencial_ruim(self):
        with self.assertRaises(HTTPException) as cm:
            app_mod.shopee_link(app_mod.LinkShopee(url="https://shopee.com.br/product/1/2"), self.u())
        self.assertEqual(cm.exception.status_code, 400)
        with self.assertRaises(HTTPException) as cm:
            app_mod.shopee_conectar(app_mod.ConectarShopee(app_id="x", secret="y"), self.u())
        self.assertEqual(cm.exception.status_code, 400)

    def test_status_nunca_expoe_o_secret(self):
        self.conectar()
        self.assertEqual(set(app_mod.shopee_status(self.u())), {"status", "mensagem", "app_id"})


class TestLegenda(unittest.TestCase):
    def test_hashtag_acompanha_a_loja_do_item(self):
        from thyfia import agente_publicacao as ag
        item = {"titulo": "Capinha iPhone", "preco": 29.9, "link_afiliado": "https://s.shopee.com.br/x", "busca": "capinha"}
        sh = ag.gerar_legenda({**item, "fonte": "shopee"})
        ml = ag.gerar_legenda({**item, "fonte": "ml"})
        self.assertIn("#shopee", sh); self.assertNotIn("#mercadolivre", sh)
        self.assertIn("#mercadolivre", ml); self.assertNotIn("#shopee", ml)


class TestFront(unittest.TestCase):
    def test_telas_da_shopee_existem(self):
        html = (ESTATICO / "index.html").read_text()
        js = (ESTATICO / "app.js").read_text()
        for trecho in ('id="fonte-escolha"', 'id="card-shopee"', 'id="form-shopee"', 'id="form-shopee-link"'):
            self.assertIn(trecho, html)
        self.assertNotIn("em-breve\"><div class=\"int-topo\"><div class=\"int-logo sh\">", html)
        for trecho in ("/api/shopee/conectar", "/api/shopee/link", 'fonte: "shopee"', "aplicarStatusShopee"):
            self.assertIn(trecho, js)

    def test_seletor_de_loja_fica_fora_do_cartao_bloqueado(self):
        """O aviso "Conecte…" cobre o cartão inteiro; se o seletor ficasse dentro, quem só tem a Shopee não conseguiria trocar de loja."""
        html = (ESTATICO / "index.html").read_text()
        self.assertLess(html.index('id="fonte-escolha"'), html.index('id="busca-card"'))

    def test_passo_da_shopee_tem_texto_no_checklist(self):
        self.assertIn("shopee:", (ESTATICO / "planos.js").read_text())


if __name__ == "__main__":
    unittest.main()
