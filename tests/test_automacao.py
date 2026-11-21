"""Testes da integração com o n8n (sem rede: urlopen é simulado).

Rodar:  .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from thyfia import automacao

URL = "https://n8n.exemplo.test/webhook/thyfia-links"
CHAVE = "token-de-teste-123"
ENV = {"N8N_WEBHOOK_URL": URL, "N8N_API_KEY": CHAVE}


def linha(i: int, link: str | None = None, preco="27,06") -> dict:
    return {"titulo": f"Carregador {i}", "preco": preco,
            "url": f"https://www.mercadolivre.com.br/p/MLB{i}",
            "link": link if link is not None else f"https://meli.la/abc{i}"}


class RespostaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def resposta_200(corpo: dict):
    return RespostaFalsa(json.dumps(corpo).encode())


def erro_http(codigo: int):
    return urllib.error.HTTPError(URL, codigo, "erro", {}, io.BytesIO(b'{"erro":"x"}'))


# --------------------------------------------------------------------------- payload
class TestPreco(unittest.TestCase):
    def test_formatos(self):
        casos = {"27,06": 27.06, "1.234,56": 1234.56, "R$ 27": 27.0, "  99,9 ": 99.9,
                 "27.06": 27.06, 27.06: 27.06, 30: 30.0, "1.234.567": 1234567.0}
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(automacao.preco_numero(entrada), esperado)

    def test_vazio_ou_invalido(self):
        for entrada in (None, "", "grátis", "R$", True):
            with self.subTest(entrada=entrada):
                self.assertIsNone(automacao.preco_numero(entrada))

    def test_preco_e_numero_no_json(self):
        item = automacao.montar_links([linha(1, preco="1.234,56")])[0]
        self.assertIsInstance(item["preco"], float)
        self.assertIn('"preco": 1234.56', json.dumps(item))


class TestMontarLinks(unittest.TestCase):
    def test_formato_do_item(self):
        item = automacao.montar_links([linha(1)])[0]
        self.assertEqual(item, {"titulo": "Carregador 1", "preco": 27.06,
                                "url": "https://www.mercadolivre.com.br/p/MLB1",
                                "afiliado": "https://meli.la/abc1"})

    def test_ignora_link_invalido(self):
        linhas = [linha(1), linha(2, link="NÃO GERADO"), linha(3, link=""),
                  {**linha(4), "link": None}, linha(5, link="meli.la/semhttp"),
                  linha(6, link="ftp://x"), linha(7, link="  https://meli.la/ok7  ")]
        afiliados = [i["afiliado"] for i in automacao.montar_links(linhas)]
        self.assertEqual(afiliados, ["https://meli.la/abc1", "https://meli.la/ok7"])

    def test_titulo_texto_simples(self):
        item = automacao.montar_links([{**linha(1), "titulo": "  Carregador\n Turbo\t45w "}])[0]
        self.assertEqual(item["titulo"], "Carregador Turbo 45w")

    def test_remove_link_repetido(self):
        self.assertEqual(len(automacao.montar_links([linha(1), linha(1)])), 1)


class TestLotes(unittest.TestCase):
    def test_lotes_de_50(self):
        payloads = automacao.montar_payloads("carregador", [linha(i) for i in range(120)])
        self.assertEqual([len(p["links"]) for p in payloads], [50, 50, 20])
        self.assertTrue(all(p["keyword"] == "carregador" for p in payloads))

    def test_exatamente_50(self):
        payloads = automacao.montar_payloads("x", [linha(i) for i in range(50)])
        self.assertEqual([len(p["links"]) for p in payloads], [50])

    def test_lote_conta_so_validos(self):
        linhas = [linha(i) for i in range(50)] + [linha(99, link="NÃO GERADO")]
        self.assertEqual(len(automacao.montar_payloads("x", linhas)), 1)

    def test_sem_links(self):
        self.assertEqual(automacao.montar_payloads("x", [linha(1, link="NÃO GERADO")]), [])


# --------------------------------------------------------------------------- envio (mock)
@mock.patch.dict(os.environ, ENV)
class TestEnvio(unittest.TestCase):
    def test_200_soma_lotes_e_headers(self):
        respostas = [
            resposta_200({"recebidos": 50, "novos": 48, "duplicados": 2, "invalidos": 0, "palavra_chave": "carregador"}),
            resposta_200({"recebidos": 10, "novos": 0, "duplicados": 9, "invalidos": 1, "palavra_chave": "carregador"}),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=respostas) as urlopen:
            r = automacao.enviar("carregador", [linha(i) for i in range(60)])
        self.assertEqual(r["novos"], 48)
        self.assertEqual(r["duplicados"], 11)
        self.assertEqual(r["recebidos"], 60)
        self.assertEqual(r["invalidos"], 1)
        self.assertEqual(r["lotes"], 2)
        self.assertEqual(urlopen.call_count, 2)
        req = urlopen.call_args_list[0].args[0]
        self.assertEqual(req.full_url, URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("X-api-key"), CHAVE)
        self.assertEqual(urlopen.call_args_list[0].kwargs["timeout"], 30)
        corpo = json.loads(req.data)
        self.assertEqual(corpo["keyword"], "carregador")
        self.assertEqual(len(corpo["links"]), 50)

    def test_200_em_lista_do_n8n(self):
        corpo = [{"recebidos": 1, "novos": 1, "duplicados": 0, "invalidos": 0, "palavra_chave": "x"}]
        with mock.patch("urllib.request.urlopen", return_value=RespostaFalsa(json.dumps(corpo).encode())):
            self.assertEqual(automacao.enviar("x", [linha(1)])["novos"], 1)

    def test_401_token_errado(self):
        with mock.patch("urllib.request.urlopen", side_effect=erro_http(401)):
            with self.assertRaises(automacao.ErroAutomacao) as ctx:
                automacao.enviar("x", [linha(1)])
        self.assertEqual(ctx.exception.codigo, "token")
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn(CHAVE, str(ctx.exception))

    def test_500_erro_do_n8n(self):
        with mock.patch("urllib.request.urlopen", side_effect=erro_http(500)):
            with self.assertRaises(automacao.ErroAutomacao) as ctx:
                automacao.enviar("x", [linha(1)])
        self.assertEqual(ctx.exception.codigo, "n8n")

    def test_timeout(self):
        for erro in (socket.timeout("timed out"), TimeoutError(),
                     urllib.error.URLError(socket.timeout("timed out"))):
            with self.subTest(erro=type(erro).__name__):
                with mock.patch("urllib.request.urlopen", side_effect=erro):
                    with self.assertRaises(automacao.ErroAutomacao) as ctx:
                        automacao.enviar("x", [linha(1)])
                self.assertEqual(ctx.exception.codigo, "rede")
                self.assertIn("30s", str(ctx.exception))

    def test_falha_de_rede(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaises(automacao.ErroAutomacao) as ctx:
                automacao.enviar("x", [linha(1)])
        self.assertEqual(ctx.exception.codigo, "rede")

    def test_sem_links_nao_chama_rede(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(automacao.ErroAutomacao) as ctx:
                automacao.enviar("x", [linha(1, link="NÃO GERADO")])
        self.assertEqual(ctx.exception.codigo, "vazio")
        urlopen.assert_not_called()


class TestConfig(unittest.TestCase):
    def test_sem_config(self):
        with mock.patch.dict(os.environ, {"N8N_WEBHOOK_URL": "", "N8N_API_KEY": ""}):
            with self.assertRaises(automacao.ErroAutomacao) as ctx:
                automacao.enviar("x", [linha(1)])
            self.assertEqual(ctx.exception.codigo, "config")
            self.assertFalse(automacao.configurada())


# --------------------------------------------------------------------------- planilha local
class TestPlanilha(unittest.TestCase):
    def test_le_formato_existente(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Data/Hora", "Busca", "Título", "Preço (R$)", "URL do Produto", "Link de Afiliado"])
        ws.append(["21/11/2026 10:00", "carregador", "Carregador A", "27,06",
                   "https://www.mercadolivre.com.br/p/MLB1", "https://meli.la/1Z2RsLz"])
        ws.append(["21/11/2026 10:00", "carregador", "Carregador B", "10,00",
                   "https://www.mercadolivre.com.br/p/MLB2", "NÃO GERADO"])
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "job.xlsx"
            wb.save(arq)
            itens = automacao.montar_links(automacao.ler_planilha(arq))
        self.assertEqual(itens, [{"titulo": "Carregador A", "preco": 27.06,
                                  "url": "https://www.mercadolivre.com.br/p/MLB1",
                                  "afiliado": "https://meli.la/1Z2RsLz"}])


if __name__ == "__main__":
    unittest.main()
