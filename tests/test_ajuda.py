"""Guia de uso dentro da plataforma (/api/ajuda) e download da extensão."""

from __future__ import annotations

import io
import re
import unittest
import zipfile
from pathlib import Path

from thyfia import app as app_mod

RAIZ = Path(app_mod.__file__).resolve().parent
U = {"id": 1, "nome": "Teste", "email": "t@t.test"}


def _rota(caminho: str):
    return next(r for r in app_mod.app.routes if getattr(r, "path", "") == caminho)


class TestAjuda(unittest.TestCase):
    def setUp(self):
        self.html = app_mod.ajuda(u=U).body.decode("utf-8")

    def test_rotas_exigem_login(self):
        for caminho in ("/api/ajuda", "/api/extensao/download"):
            deps = [d.call for d in _rota(caminho).dependant.dependencies]
            self.assertIn(app_mod.usuario_atual, deps, caminho)

    def test_guia_nao_fica_na_pasta_publica(self):
        self.assertFalse((RAIZ / "static" / "ajuda.html").exists())
        self.assertFalse(any("conteudo" in str(p) for p in (RAIZ / "static").rglob("*")))

    def test_secoes_com_id_e_titulo_unicos(self):
        ids = re.findall(r'<section class="aj-sec" id="([^"]+)" data-titulo="([^"]+)"', self.html)
        self.assertGreaterEqual(len(ids), 9)
        self.assertEqual(len({i for i, _ in ids}), len(ids))
        self.assertEqual(self.html.count("<section"), len(ids))
        self.assertEqual(self.html.count("</section>"), len(ids))

    def test_cobre_todos_os_menus(self):
        for trecho in ("Gerar links", "Integrações", "Agente de Publicação", "Histórico", "Cérebro",
                       "Enviar para a automação", "Solução de problemas"):
            self.assertIn(trecho, self.html)

    def test_botoes_levam_a_telas_que_existem(self):
        index = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
        views = set(re.findall(r'data-view="([a-z]+)"', index))
        destinos = set(re.findall(r'data-ir="([a-z]+)"', self.html))
        self.assertTrue(destinos)
        self.assertLessEqual(destinos, views)
        self.assertIn("ajuda", views)
        self.assertIn('id="view-ajuda"', index)

    def test_guia_nao_vaza_segredos(self):
        for segredo in ("thy_", "IGTOKEN", "EAAV", "x-api-key"):
            self.assertNotIn(segredo, self.html)


class TestExtensaoZip(unittest.TestCase):
    def test_zip_com_manifesto_e_dominio_de_producao(self):
        resp = app_mod.baixar_extensao(u=U)
        self.assertEqual(resp.media_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(resp.body)) as z:
            nomes = z.namelist()
            self.assertIn("extensao_thyfia/manifest.json", nomes)
            self.assertIn("extensao_thyfia/background.js", nomes)
            self.assertIn("extensao_thyfia/ponte.js", nomes)
            manifesto = z.read("extensao_thyfia/manifest.json").decode()
        self.assertIn("https://thyfia.integradata.app.br/*", manifesto)


if __name__ == "__main__":
    unittest.main()
