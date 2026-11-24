"""O que o cartão da busca do Mercado Livre mostra (selo, preço "de", desconto, "com Cupom"/"no Pix", vendas e nota) e
como isso chega ao texto da oferta. O fixture é JSON real, copiado de lista.mercadolivre.com.br em 22/11/2026."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_piloto import AGORA, Base
from thyfia import itens, mensagens, motor_busca, piloto

FIXTURE = Path(__file__).parent / "fixtures" / "cartoes_ml_busca.txt"
BARRA = chr(92) + "u002F"  # o JSON do Mercado Livre escreve "/" como \u002F
CLICK1 = BARRA.join(["click1.mercadolivre.com.br", "mclics", "clicks", "external", "MLB", "count"])


def cartoes() -> list[str]:
    linhas = [l for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.startswith('"polycard"')]
    assert len(linhas) == 2
    return linhas


ANUNCIO, ORGANICO = cartoes()
# o anúncio vira um cartão orgânico (URL de produto no lugar da de rastreio): é o caso "MAIS VENDIDO" com preço de/por
MAIS_VENDIDO = ANUNCIO.replace(CLICK1, r"www.mercadolivre.com.br/parafusadeira-tb21px/p/MLB30144703")
# NÃO veio nenhum cartão "no Pix" na amostra (só o HTML mostrava): este é o cartão "com Cupom" trocando o rótulo
NO_PIX = ORGANICO.replace('"text":"com Cupom"', '"text":"no Pix"')


class TestLeituraDoCartao(unittest.TestCase):
    def test_anuncio_patrocinado_e_descartado(self):
        self.assertEqual(motor_busca.extrair_produtos(ANUNCIO), [])

    def test_cartao_com_cupom_e_preco_de(self):
        p = motor_busca.extrair_produtos(ORGANICO)[0]
        self.assertTrue(p["titulo"].startswith("Kit Multi Ferramentas"))
        self.assertEqual(p["preco"], "621,00")
        self.assertEqual((p["preco_de"], p["desconto_pct"], p["condicao"]), (1599.0, 61, "cupom"))
        self.assertEqual((p["vendas"], p["nota"], p["selo"]), (10000, 4.7, None))

    def test_selo_mais_vendido_e_preco_de_por(self):
        p = motor_busca.extrair_produtos(MAIS_VENDIDO)[0]
        self.assertEqual((p["selo"], p["preco"], p["preco_de"], p["desconto_pct"], p["condicao"]),
                         ("MAIS VENDIDO", "139,90", 259.9, 46, None))
        self.assertEqual((p["vendas"], p["nota"]), (250000, 4.8))

    def test_cartao_no_pix(self):
        self.assertEqual(motor_busca.extrair_produtos(NO_PIX)[0]["condicao"], "pix")

    def test_condicao_desconhecida_nao_vira_nada(self):
        p = motor_busca.extrair_produtos(ORGANICO.replace('"text":"com Cupom"', '"text":"no cartão X"'))[0]
        self.assertIsNone(p["condicao"])

    def test_cartao_simples_continua_funcionando_sem_os_campos_novos(self):
        p = motor_busca.extrair_produtos('"polycard":{"metadata":{"id":"MLB5","url":"www.mercadolivre.com.br/kit/p/MLB5"},'
                                         '"title":{"text":"Kit 12"},"prc":89.9,"sales":120}')[0]
        self.assertEqual((p["selo"], p["preco_de"], p["desconto_pct"], p["condicao"]), (None, None, None, None))
        self.assertEqual(p["preco"], "89,90")

    def test_preco_de_que_nao_e_maior_que_o_atual_e_ignorado(self):
        p = motor_busca.extrair_produtos(ORGANICO.replace('"value":1599,', '"value":600,'))[0]
        self.assertEqual((p["preco_de"], p["desconto_pct"]), (None, None))


class TestTextoDaOferta(unittest.TestCase):
    BASE = {"id": 9, "titulo": "Kit Multi Ferramentas", "preco": 621.0, "busca": "furadeira",
            "link_afiliado": "https://meli.la/abc"}

    def compor(self, variante, **extra):
        return mensagens.compor({**self.BASE, **extra}, "variado", 1, variante)

    def test_com_cupom_avisa_e_mostra_de_por_em_todos_os_layouts(self):
        for v in "ABC":
            m = self.compor(v, preco_de=1599.0, desconto_pct=61, condicao="cupom", vendas=10000, nota=4.7)
            self.assertIn("~R$ 1.599,00~", m["texto"], v)
            self.assertIn("R$ 621,00", m["texto"], v)
            self.assertIn("com cupom", m["texto"], v)
            self.assertIn("61% OFF", m["texto"], v)  # A: "(61% OFF)" no preço; B: destaque na abertura; C: gancho
            self.assertIn(mensagens.AVISO_CUPOM, m["texto"], v)
            self.assertIn("⭐ 4,7 · +10 mil vendidos", m["texto"], v)
            self.assertIn("<s>R$ 1.599,00</s>", m["html"], v)
            self.assertIn("https://meli.la/abc", m["texto"], v)

    def test_no_pix_qualifica_o_preco_e_nao_fala_de_cupom(self):
        for v in "ABC":
            t = self.compor(v, condicao="pix")["texto"]
            self.assertIn("no Pix", t, v)
            self.assertNotIn("cupom", t.lower(), v)

    def test_selo_mais_vendido_e_black_friday(self):
        for v in "ABC":
            self.assertIn("Mais vendido", self.compor(v, selo="MAIS VENDIDO")["texto"], v)
        self.assertIn("🖤 Black Friday", self.compor("A", selo="BLACK FRIDAY")["texto"])
        self.assertNotIn("🏆", self.compor("A", selo="OFERTA DO DIA")["texto"])  # selo que não conhecemos não entra

    def test_sem_dados_do_cartao_o_texto_fica_como_antes(self):
        for v in "ABC":
            t = self.compor(v)["texto"]
            for proibido in ("~", "OFF", "cupom", "Pix", "⭐", "🏆", "🖤"):
                self.assertNotIn(proibido, t, (v, proibido))

    def test_prova_social_so_quando_e_boa_noticia(self):
        self.assertEqual(mensagens.prova_social({"nota": 4.8, "vendas": 250000}), "⭐ 4,8 · +250 mil vendidos")
        self.assertEqual(mensagens.prova_social({"nota": 4.6, "vendas": 1000}), "⭐ 4,6 · +1000 vendidos")
        self.assertEqual(mensagens.prova_social({"nota": 4.2, "vendas": 5000}), "")
        self.assertEqual(mensagens.prova_social({"nota": 4.9, "vendas": 120}), "")
        self.assertEqual(mensagens.prova_social({}), "")

    def test_desconto_estranho_ou_preco_de_falso_nao_aparece(self):
        self.assertNotIn("OFF", self.compor("A", preco_de=1599.0, desconto_pct=95)["texto"])  # % absurdo: só o de/por
        self.assertIn("~R$ 1.599,00~", self.compor("A", preco_de=1599.0, desconto_pct=95)["texto"])
        t = self.compor("A", preco_de=600.0, desconto_pct=61)["texto"]  # "de" menor que o atual: ignora tudo
        self.assertNotIn("~", t)
        self.assertNotIn("OFF", t)

    def test_com_de_por_do_ml_nao_repete_a_queda_do_historico(self):
        hist = {"queda_pct": 20.0, "preco_ref": 800.0}
        self.assertIn("Baixou 20%", self.compor("A", **hist)["texto"])
        t = self.compor("A", preco_de=1599.0, **hist)["texto"]
        self.assertNotIn("Baixou", t)
        self.assertIn("Menor preço", self.compor("A", preco_de=1599.0, menor_preco=True)["texto"])


class TestDoCartaoAoPiloto(Base):
    def salvar(self, titulo, n, **dados):
        itens.salvar_item("j1", "furadeira", titulo, 621, f"https://produto.mercadolivre.com.br/MLB-{n:03d}",
                          f"https://meli.la/{n:03d}", criado_em="2026-11-21T10:00:00-03:00",
                          imagem="https://http2.mlstatic.com/D_NQ_NP_2X_1-F.jpg", **dados)

    def test_item_guarda_e_devolve_os_dados_do_cartao(self):
        self.salvar("Kit A", 1, selo="MAIS VENDIDO", preco_de=1599, desconto_pct=61, condicao="cupom", vendas=10000, nota=4.7)
        o = piloto.candidatos(1, None, AGORA)[0]
        self.assertEqual((o["selo"], o["preco_de"], o["desconto_pct"], o["condicao"], o["vendas"], o["nota"]),
                         ("MAIS VENDIDO", 1599.0, 61, "cupom", 10000, 4.7))

    def test_condicao_invalida_e_selo_gigante_sao_limpos(self):
        self.salvar("Kit B", 2, selo="X" * 100, condicao="qualquer coisa")
        o = piloto.candidatos(1, None, AGORA)[0]
        self.assertIsNone(o["condicao"])
        self.assertEqual(len(o["selo"]), 40)

    def test_selo_mais_vendido_sobe_na_curadoria_e_explica(self):
        self.salvar("Kit sem selo", 3)
        self.salvar("Kit com selo", 4, selo="MAIS VENDIDO")
        por = {o["titulo"]: o for o in piloto.candidatos(1, None, AGORA)}
        com, sem = por["Kit com selo"], por["Kit sem selo"]
        self.assertAlmostEqual(com["pontos"] - sem["pontos"], 6.0, places=1)
        self.assertTrue(any("Mais vendido" in m for m in com["motivos"]))
        self.assertFalse(any("Mais vendido" in m for m in sem["motivos"]))

    def test_mensagem_na_fila_leva_o_aviso_de_cupom(self):
        self.salvar("Kit Multi Ferramentas", 5, selo="MAIS VENDIDO", preco_de=1599, desconto_pct=61, condicao="cupom",
                    vendas=10000, nota=4.7)
        self.destino()
        self.ligar()
        self.assertEqual(self.ciclo()["acao"], "planejou")
        from thyfia import db
        texto = db.conn().execute("SELECT texto FROM piloto_envios").fetchone()["texto"]
        for esperado in ("com cupom", mensagens.AVISO_CUPOM, "🏆 Mais vendido", "~R$ 1.599,00~"):
            self.assertIn(esperado, texto)

    def test_painel_mostra_selo_e_condicao(self):
        self.salvar("Kit A", 6, selo="MAIS VENDIDO", condicao="pix")
        prox = piloto.status(1)["estoque"]["proximas"][0]
        self.assertEqual((prox["selo"], prox["condicao"]), ("MAIS VENDIDO", "pix"))


if __name__ == "__main__":
    unittest.main()


class TestDadosDoCartaoNoPiloto(unittest.TestCase):
    """Itens repetidos em várias gerações: a mais nova vale, mas herda o que o cartão trouxe antes."""

    def test_geracao_do_historico_herda_dados_do_cartao_anterior(self):
        from thyfia import piloto
        nova = {"url_produto": "u1", "link_afiliado": "l1", "preco": 100.0, "preco_de": None, "selo": None,
                "condicao": None, "desconto_pct": None, "vendas": None, "nota": None, "imagem": None}
        velha = {**nova, "link_afiliado": "l0", "preco": 101.0, "preco_de": 150.0, "selo": "MAIS VENDIDO",
                 "condicao": "pix", "desconto_pct": 33, "vendas": 500, "nota": 4.8, "imagem": "http://x/i.jpg"}
        piloto._unir_dados_do_cartao([nova, velha])
        self.assertEqual(nova["preco_de"], 150.0)
        self.assertEqual(nova["condicao"], "pix")
        self.assertEqual(nova["selo"], "MAIS VENDIDO")

    def test_nao_herda_se_o_preco_mudou_muito(self):
        from thyfia import piloto
        nova = {"url_produto": "u1", "link_afiliado": "l1", "preco": 100.0, "preco_de": None, "selo": None,
                "condicao": None, "desconto_pct": None, "vendas": None, "nota": None, "imagem": None}
        velha = {**nova, "preco": 200.0, "preco_de": 300.0, "desconto_pct": 33}
        piloto._unir_dados_do_cartao([nova, velha])
        self.assertIsNone(nova["preco_de"])

    def test_resumo_da_captura(self):
        from thyfia import motor_ml
        r = motor_ml.resumo_captura([{"origem": "cartao", "preco_de": 10, "imagem": "x"},
                                     {"origem": "cartao"}, {}], 5)
        self.assertEqual((r["cartoes_brutos"], r["do_cartao"], r["do_historico"], r["com_de"]), (5, 2, 1, 1))


class TestCartaoRenderizado(unittest.TestCase):
    """O ML passou a entregar a busca como HTML renderizado (sem "polycard"): lemos o texto visível do cartão.
    Texto real capturado em 24/11/2026 em lista.mercadolivre.com.br/tenis-masculino."""

    TEXTO = ("Disponível em 3 cores\n3\nTênis Flyer Runner Mesh Bdp Puma\nPUMA\n4.9 | +10mil vendidos\n"
             "Classificação 4.9 de 5 estrelas. Mais de 10mil produtos vendidos.\nR$\n349\n,\n99\nR$\n199\n,\n99\n"
             "42% OFF\n6x\nR$\n33\n,\n33\n sem juros\n20% OFF com Saldo no Mercado Pago\n Sexta-feira | Grátis\nAd")
    HREF = ("https://click1.mercadolivre.com.br/mclics/clicks/external/MLB/count?a=abc&pdp_filters=item_id%3AMLB4477485645"
            "#polycard_client=search-desktop&wid=MLB4477485645&sid=search")
    IMG = "https://http2.mlstatic.com/D_Q_NP_2X_851053-MLA106705399464_022026-E.webp"

    def test_le_de_por_off_parcelas_vendas_nota(self):
        d = motor_busca.ler_texto_do_cartao(self.TEXTO)
        self.assertEqual((d["preco_valor"], d["preco_de"], d["desconto_pct"]), (199.99, 349.99, 42))
        self.assertEqual(d["parcelas"], "6x R$ 33,33 sem juros")
        self.assertEqual((d["vendas"], d["nota"]), (10000, 4.9))

    def test_pix_e_cupom(self):
        d = motor_busca.ler_texto_do_cartao("Tênis R$ 200 R$ 172 14% OFF no Pix")
        self.assertEqual((d["condicao"], d["preco_de"]), ("pix", 200.0))
        d = motor_busca.ler_texto_do_cartao("Tênis R$ 200 R$ 160 20% OFF com Cupom")
        self.assertEqual(d["condicao"], "cupom")

    def test_sem_desconto_so_tem_preco(self):
        d = motor_busca.ler_texto_do_cartao("Camiseta R$ 59 , 90 3x R$ 19 , 96 sem juros")
        self.assertEqual((d["preco_valor"], d["preco_de"], d["desconto_pct"]), (59.9, None, None))

    def test_produto_do_anuncio_usa_link_limpo_do_item(self):
        p = motor_busca.produtos_de_cartoes([{"url": self.HREF, "titulo": "Tênis Flyer Runner Mesh Bdp Puma",
                                              "texto": self.TEXTO, "imagem": self.IMG}])[0]
        self.assertEqual(p["url"], "https://produto.mercadolivre.com.br/MLB-4477485645")
        self.assertEqual(p["preco"], "199,99")
        self.assertEqual(p["origem"], "cartao")
        self.assertTrue(p["imagem"].endswith("851053-MLA106705399464_022026-F.jpg"))

    def test_mensagem_mostra_de_off_e_parcelas(self):
        p = motor_busca.produtos_de_cartoes([{"url": self.HREF, "titulo": "Tênis Flyer Runner Mesh Bdp Puma",
                                              "texto": self.TEXTO, "imagem": self.IMG}])[0]
        item = {"id": 1, "busca": "tenis", "titulo": p["titulo"], "preco": 199.99, "link_afiliado": "https://meli.la/x",
                "preco_de": p["preco_de"], "desconto_pct": p["desconto_pct"], "parcelas": p["parcelas"],
                "vendas": p["vendas"], "nota": p["nota"]}
        txt = mensagens.compor(item, "padrao", 1, "A")["texto"]
        self.assertIn("De ~R$ 349,99~", txt)
        self.assertIn("42% OFF", txt)
        self.assertIn("6x R$ 33,33 sem juros", txt)
