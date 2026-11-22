"""Testes da fonte de dados no banco (job_itens): geração, importação, envio e download.

Rodar:  .venv/bin/python -m unittest discover -s tests -t . -v
(o tests/__init__.py isola tudo numa pasta temporária)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook

from thyfia import automacao, db, itens, motor_ml

assert "thyfia-testes-" in str(db.DB_PATH), "testes devem usar o banco temporário"

from thyfia import app as app_mod  # noqa: E402  (importa depois do banco isolado)

CABECALHOS = ["Data/Hora", "Busca", "Título", "Preço (R$)", "URL do Produto", "Link de Afiliado"]


class RespostaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def resposta(**campos):
    base = {"recebidos": 0, "novos": 0, "duplicados": 0, "invalidos": 0, "palavra_chave": "x"}
    return RespostaFalsa(json.dumps(base | campos).encode())


def corpos(urlopen) -> list[dict]:
    return [json.loads(c.args[0].data) for c in urlopen.call_args_list]


class BaseBanco(unittest.TestCase):
    def setUp(self):
        db.init()
        c = db.conn()
        for t in ("job_itens", "jobs", "usuarios"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO usuarios (id, nome, email, senha_hash, sal) VALUES (1,'Teste','t@t','x','y')")
        c.commit()
        self.u = {"id": 1}
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def criar_job(self, job_id="job1", termo="carregador", status="concluido", arquivo=None):
        c = db.conn()
        c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, status, arquivo) VALUES (?,?,?,?,?,?)",
                  (job_id, 1, termo, 20, status, arquivo))
        c.commit()
        return job_id

    def planilha(self, linhas, cabecalhos=CABECALHOS, nome="antiga.xlsx") -> Path:
        wb = Workbook()
        ws = wb.active
        ws.append(cabecalhos)
        for l in linhas:
            ws.append(l)
        arq = self.tmp / nome
        wb.save(arq)
        return arq


# --------------------------------------------------------------------------- banco
class TestTabela(BaseBanco):
    def test_sem_duplicado_no_mesmo_job_e_nulls_permitidos(self):
        self.criar_job()
        self.assertTrue(itens.salvar_item("job1", "carregador", "A", "27,06", "u1", "https://meli.la/a"))
        self.assertFalse(itens.salvar_item("job1", "carregador", "A2", "1", "u1b", "https://meli.la/a"))
        itens.salvar_item("job1", "carregador", "B", "1", "u2", None)       # link não gerado
        itens.salvar_item("job1", "carregador", "C", "1", "u3", "NÃO GERADO")
        self.assertEqual(itens.contar("job1"), (3, 1))
        self.criar_job("job2")  # mesmo link em outro job é permitido
        self.assertTrue(itens.salvar_item("job2", "carregador", "A", "1", "u1", "https://meli.la/a"))

    def test_init_idempotente_nao_apaga(self):
        self.criar_job()
        itens.salvar_item("job1", "carregador", "A", "27,06", "u1", "https://meli.la/a")
        db.init()
        db.init()
        self.assertEqual(itens.contar("job1"), (1, 1))

    def test_preco_numerico_e_data_com_fuso(self):
        self.criar_job()
        itens.salvar_item("job1", "c", "A", "R$ 1.234,56", "u", "https://meli.la/a")
        i = itens.itens_do_job("job1")[0]
        self.assertEqual(i["preco"], 1234.56)
        self.assertRegex(i["criado_em"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-03:00$")


# --------------------------------------------------------------------------- geração
class FakePage:
    url = "https://www.mercadolivre.com.br/afiliados/linkbuilder"

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a):
        pass

    def on(self, *a):
        pass

    def evaluate(self, *a, **k):
        return {"titulo": "Gerador", "campo": "", "botoes": ["Gerar"], "texto": ""}


@contextmanager
def navegador_falso(uid, visivel):
    yield object(), FakePage()


class TestGeracao(BaseBanco):
    def test_grava_cada_item_assim_que_o_link_e_criado(self):
        self.criar_job("g1", status="iniciando")
        produtos = [{"titulo": f"Carregador {i}", "preco": "27,06", "url": f"https://www.mercadolivre.com.br/p/MLB{i}"}
                    for i in range(3)]
        vistos_no_banco = []

        def gerar_link(page, url, capturados):
            vistos_no_banco.append(itens.contar("g1")[0])  # quantos já estavam gravados
            return None if url.endswith("MLB1") else f"https://meli.la/{url[-4:]}"

        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", side_effect=gerar_link), \
             mock.patch.object(motor_ml.motor_busca, "buscar", return_value=produtos), \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "0"}):
            motor_ml.executar_job("g1", 1, "carregador", 3)

        self.assertEqual(vistos_no_banco, [0, 1, 2])  # gravou um a um, durante a geração
        job = db.conn().execute("SELECT * FROM jobs WHERE id='g1'").fetchone()
        self.assertEqual(job["status"], "concluido", job["erro"])
        self.assertIsNone(job["arquivo"])  # planilha local desligada: nada em disco
        its = itens.itens_do_job("g1")
        self.assertEqual([i["link_afiliado"] for i in its], ["https://meli.la/MLB0", None, "https://meli.la/MLB2"])
        self.assertEqual(its[0]["preco"], 27.06)
        self.assertEqual(its[0]["busca"], "carregador")

    def test_planilha_local_opcional_ligada(self):
        self.criar_job("g2", status="iniciando")
        prods = [{"titulo": "A", "preco": "10,00", "url": "https://www.mercadolivre.com.br/p/MLB9"}]
        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", return_value="https://meli.la/z"), \
             mock.patch.object(motor_ml.motor_busca, "buscar", return_value=prods), \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "1"}):
            motor_ml.executar_job("g2", 1, "carregador", 1)
        arq = db.conn().execute("SELECT arquivo FROM jobs WHERE id='g2'").fetchone()["arquivo"]
        self.assertTrue(arq and Path(arq).exists())
        ws = load_workbook(arq).active
        self.assertEqual([c.value for c in ws[1]], CABECALHOS)

    def test_usa_produtos_lidos_pela_extensao_sem_buscar_no_servidor(self):
        self.criar_job("g3", status="iniciando")
        trechos = (
            '"polycard":{"metadata":{"id":"MLB111","url":"www.mercadolivre.com.br/whey-a/p/MLB111#pos"},'
            '"title":{"text":"Whey A"},"prc":79.9,"sales":120}\n'
            '"polycard":{"metadata":{"id":"MLB222","url":"www.mercadolivre.com.br/whey-b/p/MLB222"},'
            '"title":{"text":"Whey B"},"prc":59.5}'
        )
        gerados = []

        def gerar_link(page, url, capturados):
            gerados.append(url)
            return "https://meli.la/" + url[-6:]

        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", side_effect=gerar_link), \
             mock.patch.object(motor_ml.motor_busca, "buscar", side_effect=AssertionError("não deve buscar")), \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "0"}):
            motor_ml.executar_job("g3", 1, "whey", 2, trechos=trechos)

        job = db.conn().execute("SELECT * FROM jobs WHERE id='g3'").fetchone()
        self.assertEqual(job["status"], "concluido", job["erro"])
        self.assertEqual(len(gerados), 2)
        self.assertTrue(all("mercadolivre.com.br" in u for u in gerados))
        self.assertEqual({i["titulo"] for i in itens.itens_do_job("g3")}, {"Whey A", "Whey B"})

    def test_preco_vem_de_current_price_quando_nao_ha_prc(self):
        from thyfia import motor_busca
        trechos = ('"polycard":{"metadata":{"id":"MLB5","url":"www.mercadolivre.com.br/kit/p/MLB5"},'
                   '"title":{"text":"Kit 12"},"price":{"current_price":{"value":89.9,"currency":"BRL"}}}')
        self.assertEqual(motor_busca.extrair_produtos(trechos)[0]["preco"], "89,90")

    def test_sem_trechos_ou_vazios_o_servidor_busca_sozinho(self):
        self.criar_job("g4", status="iniciando")
        prods = [{"titulo": "A", "preco": "10,00", "url": "https://www.mercadolivre.com.br/p/MLB9"}]
        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", return_value="https://meli.la/z"), \
             mock.patch.object(motor_ml.motor_busca, "buscar", return_value=prods) as busca, \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "0"}):
            motor_ml.executar_job("g4", 1, "carregador", 1, trechos="lixo sem cartões")
        busca.assert_called_once()

    def test_para_cedo_quando_nenhum_link_sai_em_5_produtos_seguidos(self):
        self.criar_job("g5", status="iniciando")
        produtos = [{"titulo": f"P{i}", "preco": "1,00", "url": f"https://www.mercadolivre.com.br/p/MLB{i}"} for i in range(20)]
        chamadas = []

        def gerar_link(page, url, capturados):
            chamadas.append(url)
            return None

        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", side_effect=gerar_link), \
             mock.patch.object(motor_ml.motor_busca, "buscar", return_value=produtos), \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "0"}):
            motor_ml.executar_job("g5", 1, "whey", 20)

        self.assertEqual(len(chamadas), 5)  # não insiste nos 20 produtos
        job = db.conn().execute("SELECT * FROM jobs WHERE id='g5'").fetchone()
        self.assertEqual(job["status"], "erro")
        self.assertIn("não respondeu", job["erro"])

    def test_nao_para_se_ja_gerou_algum_link(self):
        self.criar_job("g6", status="iniciando")
        produtos = [{"titulo": f"P{i}", "preco": "1,00", "url": f"https://www.mercadolivre.com.br/p/MLB{i}"} for i in range(9)]

        def gerar_link(page, url, capturados):
            return "https://meli.la/ok" + url[-1] if url.endswith("MLB0") else None  # 1 ok, 8 produtos sem link

        with mock.patch.object(motor_ml, "navegador", navegador_falso), \
             mock.patch.object(motor_ml, "_carregar_sessao"), \
             mock.patch.object(motor_ml.ml, "escutar_rede"), \
             mock.patch.object(motor_ml.ml, "gerar_link", side_effect=gerar_link), \
             mock.patch.object(motor_ml.motor_busca, "buscar", return_value=produtos), \
             mock.patch.dict(os.environ, {"THYFIA_SALVAR_PLANILHA": "0"}):
            motor_ml.executar_job("g6", 1, "whey", 9)

        job = db.conn().execute("SELECT * FROM jobs WHERE id='g6'").fetchone()
        self.assertEqual(job["status"], "concluido", job["erro"])
        self.assertEqual(len(itens.itens_do_job("g6")), 9)


# --------------------------------------------------------------------------- envio
class TestEnvioDoBanco(BaseBanco):
    def test_envia_do_banco_sem_arquivo_local(self):
        self.criar_job(arquivo=str(self.tmp / "nao-existe.xlsx"))
        itens.salvar_item("job1", "carregador", "Carregador A", "27,06", "https://ml/p/1", "https://meli.la/a")
        itens.salvar_item("job1", "carregador", "Carregador B", "sem preço", "https://ml/p/2", "https://meli.la/b")
        itens.salvar_item("job1", "carregador", "Falhou", "1,00", "https://ml/p/3", None)
        with mock.patch("urllib.request.urlopen", return_value=resposta(recebidos=2, novos=2)) as urlopen:
            job = app_mod.enviar_job("job1", self.u)
        self.assertEqual(urlopen.call_count, 1)
        corpo = corpos(urlopen)[0]
        # multi-tenant: o n8n recebe também PARA QUEM divulgar (cliente)
        self.assertEqual(corpo.pop("cliente")["id"], self.u["id"])
        self.assertEqual(corpo, {"keyword": "carregador", "links": [
            {"titulo": "Carregador A", "preco": 27.06, "url": "https://ml/p/1", "afiliado": "https://meli.la/a",
             "keyword": "carregador"},
            {"titulo": "Carregador B", "url": "https://ml/p/2", "afiliado": "https://meli.la/b",
             "keyword": "carregador"},  # sem "preco"
        ]})
        self.assertEqual(job["envio_status"], "enviado")
        self.assertEqual(job["envio_resumo"]["novos"], 2)

    def test_lotes_de_50_a_partir_do_banco(self):
        self.criar_job()
        for i in range(120):
            itens.salvar_item("job1", "carregador", f"P{i}", "1,00", f"u{i}", f"https://meli.la/{i}")
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: resposta(novos=1)) as urlopen:
            app_mod.enviar_job("job1", self.u)
        self.assertEqual([len(c["links"]) for c in corpos(urlopen)], [50, 50, 20])

    def test_job_sem_itens_erro_claro_sem_chamar_n8n(self):
        self.criar_job()
        itens.salvar_item("job1", "carregador", "Falhou", "1,00", "u", None)  # só item sem link
        self.criar_job("vazio")
        for job_id in ("job1", "vazio"):
            with self.subTest(job=job_id), mock.patch("urllib.request.urlopen") as urlopen:
                with self.assertRaises(HTTPException) as ctx:
                    app_mod.enviar_job(job_id, self.u)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("Geração sem links", ctx.exception.detail)
                urlopen.assert_not_called()
                st = db.conn().execute("SELECT envio_status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                self.assertEqual(st, "nao_enviado")

    def test_buscas_diferentes_viram_chamadas_separadas(self):
        self.criar_job()
        itens.salvar_item("job1", "carregador", "A", "1,00", "u1", "https://meli.la/a")
        itens.salvar_item("job1", "fone", "B", "2,00", "u2", "https://meli.la/b")
        itens.salvar_item("job1", "carregador", "C", "3,00", "u3", "https://meli.la/c")
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: resposta(novos=1)) as urlopen:
            job = app_mod.enviar_job("job1", self.u)
        enviados = [(c["keyword"], [l["afiliado"] for l in c["links"]]) for c in corpos(urlopen)]
        self.assertEqual(enviados, [("carregador", ["https://meli.la/a", "https://meli.la/c"]),
                                    ("fone", ["https://meli.la/b"])])
        # cada linha leva a categoria certa, igual à keyword do topo da sua chamada
        for c in corpos(urlopen):
            self.assertTrue(all(l["keyword"] == c["keyword"] for l in c["links"]))
        self.assertEqual(job["envio_resumo"]["novos"], 2)


# --------------------------------------------------------------------------- importação
class TestImportacao(BaseBanco):
    def test_importa_geracao_antiga_e_envia(self):
        """Planilha com exatamente os 6 cabeçalhos → banco → payload do webhook."""
        arq = self.planilha([
            ["21/11/2026 10:05", "carregador", "Carregador Turbo 45w", "R$ 27,06",
             "https://www.mercadolivre.com.br/p/MLB1", "https://meli.la/1Z2RsLz"],
            ["21/11/2026 10:06", "carregador", "Carregador Mesa", 1234.5,
             "https://www.mercadolivre.com.br/p/MLB2", "https://meli.la/1djNSEV"],
            ["21/11/2026 10:07", "carregador", "Sem link", "10,00",
             "https://www.mercadolivre.com.br/p/MLB3", "NÃO GERADO"],
        ])
        self.criar_job(arquivo=str(arq))
        res = itens.importar_antigas()
        self.assertEqual(res, {"importados": 1, "itens": 3, "pulados": 0})
        its = itens.itens_do_job("job1")
        self.assertEqual(its[0]["criado_em"], "2026-11-21T10:05:00-03:00")  # horário de São Paulo
        self.assertEqual([i["preco"] for i in its], [27.06, 1234.5, 10.0])
        self.assertIsNone(its[2]["link_afiliado"])

        arq.unlink()  # prova: o envio não precisa mais do arquivo
        with mock.patch("urllib.request.urlopen", return_value=resposta(recebidos=2, novos=2)) as urlopen:
            app_mod.enviar_job("job1", self.u)
        corpo = corpos(urlopen)[0]
        self.assertEqual(corpo["keyword"], "carregador")
        self.assertEqual(len(corpo["links"]), 2)
        primeiro = corpo["links"][0]
        self.assertEqual(primeiro["titulo"], "Carregador Turbo 45w")
        self.assertIsInstance(primeiro["preco"], float)
        self.assertEqual(primeiro["preco"], 27.06)
        self.assertEqual(primeiro["afiliado"], "https://meli.la/1Z2RsLz")
        self.assertEqual(set(primeiro), {"titulo", "preco", "url", "afiliado", "keyword"})
        self.assertEqual(primeiro["keyword"], "carregador")

    def test_le_uma_vez_so(self):
        arq = self.planilha([["21/11/2026 10:05", "carregador", "A", "1,00", "u", "https://meli.la/a"]])
        self.criar_job(arquivo=str(arq))
        itens.importar_antigas()
        self.assertEqual(itens.importar_antigas()["importados"], 0)
        self.assertEqual(itens.contar("job1"), (1, 1))

    def test_cabecalhos_sem_acento_maiusculas_e_espacos(self):
        cab = ["  DATA/HORA ", "busca", "Titulo", "Preco  (R$)", "url do produto", " LINK DE AFILIADO "]
        arq = self.planilha([["21/11/2026 10:05", "fone", "Fone X", "99,90", "u", "https://meli.la/f"]], cab)
        self.criar_job(arquivo=str(arq))
        itens.importar_antigas()
        i = itens.itens_do_job("job1")[0]
        self.assertEqual((i["busca"], i["titulo"], i["preco"], i["link_afiliado"]),
                         ("fone", "Fone X", 99.9, "https://meli.la/f"))

    def test_sem_coluna_de_link_pula_com_aviso(self):
        arq = self.planilha([["21/11/2026", "c", "A", "1", "u"]], CABECALHOS[:5])
        self.criar_job(arquivo=str(arq))
        with self.assertLogs("thyfia.itens", "WARNING") as logs:
            res = itens.importar_antigas()
        self.assertEqual(res["pulados"], 1)
        self.assertIn("Link de Afiliado", "\n".join(logs.output))
        self.assertEqual(itens.contar("job1"), (0, 0))

    def test_arquivo_faltando_pula_com_aviso(self):
        self.criar_job(arquivo=str(self.tmp / "sumiu.xlsx"))
        self.criar_job("job2", arquivo=str(self.planilha([["", "c", "A", "1", "u", "https://meli.la/a"]])))
        with self.assertLogs("thyfia.itens", "WARNING") as logs:
            res = itens.importar_antigas()
        self.assertEqual((res["importados"], res["pulados"]), (1, 1))
        self.assertIn("não encontrada", "\n".join(logs.output))

    def test_preco_invalido_vira_nulo(self):
        arq = self.planilha([["21/11/2026 10:05", "c", "A", "consulte", "u", "https://meli.la/a"]])
        self.criar_job(arquivo=str(arq))
        itens.importar_antigas()
        self.assertIsNone(itens.itens_do_job("job1")[0]["preco"])


# --------------------------------------------------------------------------- download
class TestDownload(BaseBanco):
    def test_planilha_gerada_do_banco(self):
        self.criar_job(arquivo=None)
        itens.salvar_item("job1", "carregador", "Carregador A", "1.234,56", "https://ml/p/1",
                          "https://meli.la/a", criado_em="2026-11-21T10:05:00-03:00")
        itens.salvar_item("job1", "carregador", "Sem link", None, "https://ml/p/2", None,
                          criado_em="2026-11-21T13:06:00+00:00")  # UTC → 10:06 em SP
        resp = app_mod.baixar_planilha("job1", self.u)
        self.assertIn("thyfia-links-carregador.xlsx", resp.headers["content-disposition"])
        ws = load_workbook(io.BytesIO(resp.body)).active
        linhas = [[c.value for c in r] for r in ws.iter_rows()]
        self.assertEqual(linhas[0], CABECALHOS)
        self.assertEqual(linhas[1], ["21/11/2026 10:05", "carregador", "Carregador A", 1234.56,
                                     "https://ml/p/1", "https://meli.la/a"])
        self.assertEqual(ws.cell(row=2, column=4).number_format, "#,##0.00")
        self.assertEqual(linhas[2], ["21/11/2026 10:06", "carregador", "Sem link", None,
                                     "https://ml/p/2", "NÃO GERADO"])

    def test_sem_itens_serve_arquivo_existente(self):
        arq = self.planilha([["x", "c", "A", "1", "u", "https://meli.la/a"]])
        self.criar_job(arquivo=str(arq))
        resp = app_mod.baixar_planilha("job1", self.u)
        self.assertEqual(Path(resp.path), arq)

    def test_sem_itens_e_sem_arquivo_404(self):
        self.criar_job(arquivo=None)
        with self.assertRaises(HTTPException) as ctx:
            app_mod.baixar_planilha("job1", self.u)
        self.assertEqual(ctx.exception.status_code, 404)


# --------------------------------------------------------------------------- limpeza
class TestLimpeza(BaseBanco):
    def test_desligada_por_padrao(self):
        arq = self.planilha([["x", "c", "A", "1", "u", "https://meli.la/a"]])
        self.criar_job(arquivo=str(arq))
        itens.importar_antigas()
        self.assertEqual(itens.limpar_planilhas(), 0)
        self.assertTrue(arq.exists())

    def test_apaga_antigas_so_se_itens_no_banco(self):
        velha = self.planilha([["x", "c", "A", "1", "u", "https://meli.la/a"]], nome="velha.xlsx")
        sem_itens = self.planilha([["x", "c", "A", "1", "u", "https://meli.la/b"]], nome="sem.xlsx")
        nova = self.planilha([["x", "c", "A", "1", "u", "https://meli.la/c"]], nome="nova.xlsx")
        self.criar_job("a", arquivo=str(velha))
        self.criar_job("b", arquivo=str(sem_itens))
        self.criar_job("c", arquivo=str(nova))
        itens.importar_planilha("a", "c", velha)
        itens.importar_planilha("c", "c", nova)
        dez_dias = time.time() - 10 * 86400
        for f in (velha, sem_itens):
            os.utime(f, (dez_dias, dez_dias))
        self.assertEqual(itens.limpar_planilhas(7), 1)
        self.assertFalse(velha.exists())
        self.assertTrue(sem_itens.exists())  # sem itens no banco: nunca apaga
        self.assertTrue(nova.exists())


if __name__ == "__main__":
    unittest.main()
