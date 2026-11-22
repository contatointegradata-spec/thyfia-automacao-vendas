"""Aprendizado do Cérebro (sem LLM): memória de preços, classificador de nicho que aprende, feedback (cliques e
aprovações) virando bônus na curadoria e layout de mensagem, e o clique rastreado."""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException

from tests.test_piloto import AGORA, Base as BasePiloto, Gravador  # noqa: F401  (Gravador: fixture herdada)
from thyfia import app as app_mod
from thyfia import aprendizado, cerebro, db, imagem_host, mensagens, nichos, piloto

HOJE = datetime.now()


def dia(d: int) -> str:
    return (HOJE - timedelta(days=d)).strftime("%Y-%m-%d")


class Base(BasePiloto):
    def setUp(self):
        super().setUp()
        c = db.conn()
        for t in ("preco_historico", "nb_tokens", "nb_docs", "nicho_busca", "cerebro_bandit", "piloto_cliques"):
            c.execute(f"DELETE FROM {t}")
        c.commit()


class TestPrecos(Base):
    def test_parse(self):
        casos = {"R$ 1.299,90": 1299.9, "99,90": 99.9, "99.90": 99.9, "1.299": 1299.0, 49: 49.0, "": None, "grátis": None,
                 0: None, None: None}
        for entrada, esperado in casos.items():
            self.assertEqual(aprendizado.parse_preco(entrada), esperado, entrada)

    def hist(self, url, precos):  # precos: {dias_atras: preco}
        for d, p in precos.items():
            aprendizado.registrar_precos([{"url": url, "preco": p}], HOJE - timedelta(days=d))

    def oferta_de(self, url, preco, d=0):
        return {"url_produto": url, "preco": preco, "criado_em": f"{dia(d)}T10:00:00-03:00"}

    def test_queda_real_e_menor_preco(self):
        self.hist("u1", {4: 129.9, 3: 129.9, 2: 125.0})
        info = aprendizado.precos_info([self.oferta_de("u1", 99.9)])["u1"]
        self.assertEqual(info["obs"], 3)
        self.assertEqual(info["ref"], 129.9)  # mediana
        self.assertAlmostEqual(info["queda_pct"], 23.1, 1)
        self.assertTrue(info["menor"])

    def test_oscilacao_pequena_e_pouco_historico_nao_afirmam_nada(self):
        self.hist("u2", {4: 100.0, 3: 100.0, 2: 100.0})
        self.assertEqual(aprendizado.precos_info([self.oferta_de("u2", 97.0)])["u2"]["queda_pct"], 0.0)  # 3% = ruído
        self.hist("u3", {3: 100.0})  # um dia só: não dá para afirmar nada
        self.assertNotIn("u3", aprendizado.precos_info([self.oferta_de("u3", 50.0)]))

    def test_so_compara_com_dias_anteriores_a_oferta(self):
        self.hist("u4", {5: 200.0, 4: 200.0, 0: 120.0})  # o preço de hoje não entra na referência
        self.assertEqual(aprendizado.precos_info([self.oferta_de("u4", 120.0)])["u4"]["ref"], 200.0)

    def test_reconstroi_a_partir_dos_itens_ja_gerados_uma_vez(self):
        self.item("j1", "Produto", 80.0, "https://produto.mercadolivre.com.br/MLB-001", criado="2026-11-18T10:00:00-03:00")
        self.item("j1", "Produto", 70.0, "https://produto.mercadolivre.com.br/MLB-001", link="https://meli.la/b",
                  criado="2026-11-19T10:00:00-03:00")
        self.assertEqual(aprendizado.reconstruir_precos(), 2)
        self.assertEqual(aprendizado.reconstruir_precos(), 0)  # só na primeira vez

    def test_aprender_busca_grava_precos(self):
        cerebro.aprender_busca("fone", [{"url": "uf", "titulo": "Fone", "preco": "R$ 89,90", "ok": True}])
        self.assertEqual(db.conn().execute("SELECT preco FROM preco_historico WHERE url='uf'").fetchone()[0], 89.9)

    def test_curadoria_premia_queda_e_a_mensagem_so_mostra_o_comprovado(self):
        self.hist("https://produto.mercadolivre.com.br/MLB-001", {4: 150.0, 3: 150.0})
        a = self.oferta("Fone Com Queda", 100.0, 1, busca="fone")
        b = self.oferta("Fone Sem Historico", 100.0, 2, busca="fone")
        ranque = cerebro.curar_ofertas([dict(a=0, **x) for x in
                                        db.conn().execute("SELECT id, busca, titulo, preco, url_produto, link_afiliado, "
                                                          "imagem, criado_em FROM job_itens ORDER BY id").fetchall()])
        top = ranque[0]
        self.assertEqual(top["titulo"], "Fone Com Queda")
        self.assertTrue(any("caiu 33%" in m for m in top["motivos"]))
        self.assertEqual(ranque[1]["queda_pct"], 0.0)
        txt = mensagens.compor(top, "direto", 1)["texto"]
        self.assertIn("📉 Baixou 33%", txt)
        self.assertNotIn("Baixou", mensagens.compor(ranque[1], "direto", 1)["texto"])

    def test_mensagem_menor_preco(self):
        t = mensagens.compor({"id": 1, "titulo": "Fone", "preco": 90.0, "busca": "fone", "link_afiliado": "https://x",
                              "menor_preco": True}, "direto", 1)
        self.assertIn("Menor preço que registramos", t["texto"])
        self.assertIn("Menor preço que registramos", t["html"])


class TestClassificadorQueAprende(Base):
    def test_palavra_nova_vira_nicho_depois_de_aparecer_junto_de_uma_pista(self):
        self.assertEqual(aprendizado.nicho_de(1, "monohidratada"), (None, ""))  # nunca vista
        aprendizado.auto_treinar("creatina monohidratada", [{"titulo": "Creatina Monohidratada 300g Pura"}] * 2)
        self.assertEqual(aprendizado.nicho_de(1, "monohidratada"), ("fit", "aprendido"))

    def test_palavra_chave_manda_e_modelo_nao_chuta_com_pouca_confianca(self):
        self.assertEqual(aprendizado.nicho_de(1, "whey protein"), ("fit", "palavra-chave"))
        # palavra aprendida em dois nichos com o mesmo peso = ambígua = sem nicho
        aprendizado.treinar("kit premium", "", "fit")
        aprendizado.treinar("kit premium", "", "casa")
        self.assertEqual(aprendizado.nicho_de(1, "premium")[0], None)

    def test_so_treina_com_pista_clara(self):
        antes = db.conn().execute("SELECT COUNT(*) FROM nb_tokens").fetchone()[0]
        self.assertEqual(aprendizado.auto_treinar("xyz qwerty", [{"titulo": "Coisa Qualquer"}]), 0)
        self.assertEqual(aprendizado.auto_treinar("tenis corrida", [{"titulo": "Tênis"}]), 0)  # fit x moda: empatou
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM nb_tokens").fetchone()[0], antes)

    def test_correcao_do_cliente_vale_para_ele_e_ensina(self):
        aprendizado.corrigir_nicho(1, "Garrafa Térmica", "fit")
        self.assertEqual(aprendizado.nicho_de(1, "garrafa termica"), ("fit", "correção sua"))
        self.assertEqual(aprendizado.nicho_de(2, "garrafa termica")[0], "casa")  # outro cliente: palavra-chave
        aprendizado.corrigir_nicho(1, "garrafa termica", "")
        self.assertEqual(aprendizado.nicho_de(1, "garrafa termica")[0], "casa")
        with self.assertRaises(ValueError):
            aprendizado.corrigir_nicho(1, "x", "inexistente")

    def test_piloto_usa_o_nicho_aprendido_e_a_correcao(self):
        aprendizado.auto_treinar("creatina monohidratada", [{"titulo": "Creatina Monohidratada"}] * 2)
        self.oferta("Monohidratada Pura 300g", 80.0, 1, busca="monohidratada")
        self.oferta("Coisa Estranha", 50.0, 2, busca="coisa estranha")
        por = {o["busca"]: o for o in piloto.candidatos(1)}
        self.assertEqual((por["monohidratada"]["nicho"], por["monohidratada"]["nicho_origem"]), ("fit", "aprendido"))
        self.assertIsNone(por["coisa estranha"]["nicho"])
        app_mod.piloto_nicho_busca(app_mod.PilotoNichoBusca(busca="coisa estranha", nicho="casa"), u={"id": 1})
        por = {o["busca"]: o for o in piloto.candidatos(1)}
        self.assertEqual((por["coisa estranha"]["nicho"], por["coisa estranha"]["nicho_origem"]), ("casa", "correção sua"))


class TestFeedback(Base):
    def ensinar(self, dim_ok, dim_ruim, n=10, ok=8, ruim=1):
        for i in range(n):
            aprendizado.registrar_resultado(1, dim_ok, "whey protein", 90.0, "B", i < ok)
            aprendizado.registrar_resultado(1, dim_ruim, "fone bluetooth", 90.0, "A", i < ruim)

    def test_partida_a_frio_nao_muda_nada(self):
        b = aprendizado.Bandit(1)
        self.assertEqual(aprendizado.bonus(b, {"nicho": "fit", "busca": "whey", "preco": 50}), (0.0, []))
        self.assertIsNone(aprendizado.escolher_variante(1, random.Random(1)))

    def test_aprende_o_que_o_publico_responde_e_explica(self):
        self.ensinar("fit", "tech")
        b = aprendizado.Bandit(1)
        pf, mf = aprendizado.bonus(b, {"nicho": "fit", "busca": "whey protein", "preco": 90.0})
        pt, mt = aprendizado.bonus(b, {"nicho": "tech", "busca": "fone bluetooth", "preco": 90.0})
        self.assertGreater(pf, 8)
        self.assertLess(pt, -8)
        self.assertTrue(any("responde melhor" in m and "Fit e saúde" in m for m in mf))
        self.assertTrue(any("responde pior" in m for m in mt))

    def test_bonus_entra_na_curadoria(self):
        self.ensinar("fit", "tech")
        self.oferta("Whey Protein 900g", 90.0, 1, busca="whey protein")
        self.oferta("Fone Bluetooth TWS", 90.0, 2, busca="fone bluetooth")
        ranque = piloto.candidatos(1)
        self.assertEqual(ranque[0]["busca"], "whey protein")
        self.assertTrue(any("seu público" in m for m in ranque[0]["motivos"]))
        outro = cerebro.curar_ofertas([{k: v for k, v in o.items() if k not in ("pontos", "motivos")} for o in ranque])
        self.assertEqual(len(outro), 2)  # sem uid: sem bônus aprendido (cada público é um público)
        self.assertTrue(all("seu público" not in m for o in outro for m in o["motivos"]))

    def test_exploracao_decai_com_a_amostra(self):
        self.ensinar("fit", "tech")  # já há evidência geral na dimensão
        b = aprendizado.Bandit(1)
        novo = aprendizado.bonus(b, {"nicho": None, "busca": "air fryer", "preco": 90.0})
        self.assertTrue(any("explorando" in m for m in novo[1]))
        conhecido = aprendizado.bonus(b, {"nicho": None, "busca": "whey protein", "preco": 90.0})
        self.assertFalse(any("explorando" in m for m in conhecido[1]))

    def test_layout_que_mais_rende_domina_mas_mantem_variedade(self):
        for i in range(12):
            aprendizado.registrar_resultado(1, None, "x", 50, "B", i < 9)
            aprendizado.registrar_resultado(1, None, "x", 50, "A", i < 2)
            aprendizado.registrar_resultado(1, None, "x", 50, "C", i < 3)
        escolhas = [aprendizado.escolher_variante(1, random.Random(i)) for i in range(200)]
        self.assertGreater(escolhas.count("B"), 100)
        self.assertEqual(set(escolhas) - {"B", None}, set())
        self.assertGreater(escolhas.count(None), 30)  # 35% continua livre

    def test_compositor_respeita_a_variante(self):
        item = {"id": 5, "titulo": "Fone", "preco": 50.0, "busca": "fone", "link_afiliado": "https://x"}
        self.assertEqual({mensagens.compor(item, "direto", s, "C")["layout"] for s in range(20)}, {"C"})
        self.assertGreater(len({mensagens.compor(item, "direto", s)["layout"] for s in range(40)}), 1)


class TestSinaisDoPiloto(Base):
    def setUp(self):
        super().setUp()
        self.destino()
        self.oferta("Whey Protein 900g", 90.0, 1, busca="whey protein")
        self.url = "https://publica.test"
        p = mock.patch.object(imagem_host, "url_base_publica", lambda: self.url)
        p.start()
        self.addCleanup(p.stop)

    def test_aprovar_e_pular_ensinam_o_gosto_do_dono(self):
        self.ligar(modo="aprovar")
        self.ciclo()
        item = db.conn().execute("SELECT item_id FROM piloto_envios").fetchone()[0]
        piloto.pular(1, item)
        t, s = aprendizado.Bandit(1).get("busca", "whey protein")
        self.assertEqual((t, s), (aprendizado.PESO_APROVACAO, 0.0))
        db.conn().execute("DELETE FROM piloto_envios")
        db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
        db.conn().commit()
        self.oferta("Whey Isolado", 99.0, 2, busca="whey protein")
        self.ciclo()
        piloto.aprovar(1, db.conn().execute("SELECT item_id FROM piloto_envios").fetchone()[0], AGORA)
        t, s = aprendizado.Bandit(1).get("busca", "whey protein")
        self.assertEqual((t, s), (2 * aprendizado.PESO_APROVACAO, aprendizado.PESO_APROVACAO))

    def test_rastrear_exige_endereco_publico(self):
        self.url = ""
        with self.assertRaises(piloto.ErroPiloto) as e:
            piloto.salvar_config(1, {"rastrear": True})
        self.assertEqual(e.exception.status, 503)

    def test_link_rastreado_clique_de_gente_e_maturacao(self):
        self.ligar(rastrear=True)
        self.assertTrue(piloto.config(1)["rastrear"])
        self.ciclo()
        e = db.conn().execute("SELECT * FROM piloto_envios").fetchone()
        self.assertEqual(e["link"], f"https://publica.test/r/{e['token']}")
        self.assertIn(e["link"], e["texto"])
        self.assertEqual(e["link_real"], "https://meli.la/001")
        self.assertNotIn("meli.la", e["texto"])

        robo = "WhatsApp/2.24 A"
        gente = "Mozilla/5.0 (Linux; Android 14) Chrome/120"
        self.assertEqual(aprendizado.registrar_clique(e["token"], "1.1.1.1", robo), "https://meli.la/001")
        n = lambda: db.conn().execute("SELECT COUNT(*) FROM piloto_cliques").fetchone()[0]  # noqa: E731
        self.assertEqual(n(), 0)  # pré-visualização não é clique
        aprendizado.registrar_clique(e["token"], "2.2.2.2", gente)
        aprendizado.registrar_clique(e["token"], "2.2.2.2", gente)  # mesmo visitante
        self.assertEqual(n(), 1)
        aprendizado.registrar_clique(e["token"], "3.3.3.3", gente)
        self.assertEqual(n(), 2)
        self.assertIsNone(aprendizado.registrar_clique("naoexiste", "1.1.1.1", gente))

        # maturação: só depois de 24h; vira sucesso (teve clique) e não conta duas vezes
        db.conn().execute("UPDATE piloto_envios SET status='enviado', enviado_em=?", (piloto._iso(AGORA - timedelta(hours=5)),))
        db.conn().commit()
        self.assertEqual(aprendizado.maturar(1, AGORA), 0)
        db.conn().execute("UPDATE piloto_envios SET enviado_em=?", (piloto._iso(AGORA - timedelta(hours=30)),))
        db.conn().commit()
        self.assertEqual(aprendizado.maturar(1, AGORA), 1)
        self.assertEqual(aprendizado.maturar(1, AGORA), 0)
        self.assertEqual(aprendizado.Bandit(1).get("busca", "whey protein"), (1.0, 1.0))
        self.assertEqual(aprendizado.Bandit(1).get("nicho", "fit")[1], 1.0)

    def test_envio_sem_clique_vira_falha_na_maturacao(self):
        self.ligar(rastrear=True)
        self.ciclo()
        db.conn().execute("UPDATE piloto_envios SET status='enviado', enviado_em=?", (piloto._iso(AGORA - timedelta(hours=30)),))
        db.conn().commit()
        aprendizado.maturar(1, AGORA)
        self.assertEqual(aprendizado.Bandit(1).get("busca", "whey protein"), (1.0, 0.0))

    def test_sem_rastreio_o_link_continua_o_de_afiliado(self):
        self.ligar()
        self.ciclo()
        e = db.conn().execute("SELECT * FROM piloto_envios").fetchone()
        self.assertEqual((e["token"], e["link_real"]), (None, None))
        self.assertEqual(e["link"], "https://meli.la/001")
        self.assertIn(e["variante"], ("A", "B", "C"))

    def test_rota_de_redirecionamento(self):
        self.ligar(rastrear=True)
        self.ciclo()
        tk = db.conn().execute("SELECT token FROM piloto_envios").fetchone()[0]
        req = mock.Mock(headers={"user-agent": "Mozilla/5.0 Chrome", "x-forwarded-for": "9.9.9.9, 10.0.0.1"},
                        client=mock.Mock(host="172.0.0.1"))
        r = app_mod.clique_rastreado(tk, req)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "https://meli.la/001"))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM piloto_cliques").fetchone()[0], 1)
        with self.assertRaises(HTTPException):
            app_mod.clique_rastreado("naoexiste", req)

    def test_resumo_do_que_foi_aprendido(self):
        aprendizado.auto_treinar("creatina monohidratada", [{"titulo": "Creatina Monohidratada"}] * 4)
        for i in range(8):
            aprendizado.registrar_resultado(1, "fit", "whey protein", 90.0, "B", i < 6)
        for d, p in ((4, 150.0), (3, 150.0), (0, 100.0)):
            aprendizado.registrar_precos([{"url": "uq", "preco": p}], HOJE - timedelta(days=d))
        cerebro.aprender_busca("x", [])  # não quebra com lista vazia
        db.conn().execute("INSERT INTO produtos (url, titulo, titulo_norm) VALUES ('uq','Produto em queda','produto queda')")
        db.conn().commit()
        r = aprendizado.resumo(1)
        self.assertEqual(r["fase"], "aprendendo")
        self.assertEqual(r["cliques"]["taxa"], 75)
        self.assertEqual(r["preferencias"]["nicho"][0]["chave"], "fit")
        self.assertIn("monohidratada", r["palavras_aprendidas"]["fit"]["palavras"])
        self.assertEqual(r["precos"]["quedas"][0]["pct"], 33)
        self.assertFalse(r["rastrear"])


if __name__ == "__main__":
    unittest.main()
