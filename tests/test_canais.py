"""Agente de Publicação por canal e formato: Instagram Story e Carrossel, conexão do Facebook.
A Graph API é sempre falsa: nada é publicado."""

from __future__ import annotations

import io
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from tests import test_publicacao as tp
from thyfia import agente_publicacao as ag
from thyfia import cofre, db, facebook, imagem_host, instagram, itens, tenant
from thyfia import app as app_mod


def jpeg_bytes(w=600, h=400, cor="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), cor).save(buf, "JPEG")
    return buf.getvalue()


class GraphMulti:
    """Graph API falsa que aceita vários containers (carrossel): C1, C2, C3… e publica M1."""

    def __init__(self):
        self.chamadas: list[tuple[str, str, dict]] = []
        self.n = 0
        self.lock = threading.Lock()

    def _rota(self, metodo, url, dados):
        with self.lock:
            self.chamadas.append((metodo, url, dict(dados)))
            caminho = url.split("/v21.0/")[1]
            if metodo == "GET" and caminho == tp.UID_IG:
                return tp.Resp({"id": tp.UID_IG, "username": "achei_baratinho_oficial"})
            if caminho.endswith("content_publishing_limit"):
                return tp.Resp({"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]})
            if metodo == "POST" and caminho.endswith("/media"):
                self.n += 1
                return tp.Resp({"id": f"C{self.n}"})
            if metodo == "GET" and caminho.startswith("C"):
                return tp.Resp({"status_code": "FINISHED"})
            if metodo == "POST" and caminho.endswith("/media_publish"):
                return tp.Resp({"id": "M1"})
        return tp.Resp({"error": {"message": "rota desconhecida", "code": 100}}, 400)

    def get(self, url, params=None, timeout=None):
        return self._rota("GET", url, params or {})

    def post(self, url, data=None, timeout=None):
        return self._rota("POST", url, data or {})

    def medias(self):
        return [d for m, u, d in self.chamadas if m == "POST" and u.endswith(f"{tp.UID_IG}/media")]

    def aplicar(self, teste):
        for nome in ("get", "post"):
            p = mock.patch.object(instagram.requests, nome, getattr(self, nome))
            p.start()
            teste.addCleanup(p.stop)
        return self


class BaseCanais(tp.Base):
    def setUp(self):
        super().setUp()
        self.job("j1")
        self.futuro = (datetime.now(itens.TZ_SP) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        self.links_publicos: list[str] = []
        self.dimensoes: list[tuple[int, int]] = []

        def url_publica(foto, pasta):  # em vez de subir para um serviço de link temporário
            arq = imagem_host.arquivo_local(foto, pasta)
            if arq:
                self.dimensoes.append(Image.open(arq).size)
            self.links_publicos.append(foto)
            return f"https://publico.example/{len(self.links_publicos)}.jpg"

        for alvo, novo in ((imagem_host, "url_para_instagram"), ):
            p = mock.patch.object(alvo, novo, url_publica)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(imagem_host, "_bytes_da_imagem", lambda url, pasta: jpeg_bytes())
        p.start()
        self.addCleanup(p.stop)

    def foto_local(self) -> str:
        return imagem_host.salvar_upload(jpeg_bytes(), db.MIDIA)["local_url"]


class TestStory(BaseCanais):
    def test_story_publica_foto_9x16_sem_legenda(self):
        g = GraphMulti().aplicar(self)
        p = ag.publicar_agora(1, None, "", tp.JPG, True, "instagram", "story")
        self.assertEqual((p["status"], p["formato"], p["canal"]), ("publicado", "story", "instagram"))
        m = g.medias()[0]
        self.assertEqual(m["media_type"], "STORIES")
        self.assertNotIn("caption", m)
        self.assertEqual(m["image_url"], "https://publico.example/1.jpg")
        self.assertEqual(self.dimensoes, [(1080, 1920)])
        self.assertEqual(g.chamadas[-1][1].split("/v21.0/")[1], f"{tp.UID_IG}/media_publish")

    def test_story_nao_bloqueia_o_item_para_o_feed(self):
        it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        GraphMulti().aplicar(self)
        ag.agendar(1, it, "", tp.JPG, self.futuro, "instagram", "story")
        self.assertEqual([i["id"] for i in ag.itens_elegiveis(1)], [it])  # ainda candidato
        ag.agendar(1, it, "Olha!", tp.JPG, self.futuro, "instagram", "feed")
        self.assertEqual(ag.itens_elegiveis(1), [])  # o feed bloqueia

    def test_feed_continua_exigindo_legenda(self):
        with self.assertRaises(ag.ErroPublicacao):
            ag.agendar(1, None, "  ", tp.JPG, self.futuro, "instagram", "feed")


class TestCarrossel(BaseCanais):
    def fotos(self, n, itens_ids=()):
        return [{"url": tp.JPG, "item_id": itens_ids[i] if i < len(itens_ids) else None} for i in range(n)]

    def test_publica_filhos_depois_o_carrossel(self):
        g = GraphMulti().aplicar(self)
        p = ag.publicar_agora(1, None, "Achados!", "", True, "instagram", "carrossel", self.fotos(3))
        self.assertEqual(p["status"], "publicado")
        ms = g.medias()
        self.assertEqual([m.get("is_carousel_item") for m in ms[:3]], ["true"] * 3)
        self.assertTrue(all("caption" not in m for m in ms[:3]))
        self.assertEqual((ms[3]["media_type"], ms[3]["children"], ms[3]["caption"]), ("CAROUSEL", "C1,C2,C3", "Achados!"))
        self.assertEqual(self.dimensoes, [(1080, 1080)] * 3)  # até as fotos remotas são baixadas e viram quadradas
        self.assertEqual(sum(1 for m, u, _ in g.chamadas if u.endswith("media_publish")), 1)

    def test_fotos_do_carrossel_ficam_quadradas(self):
        GraphMulti().aplicar(self)
        locais = [self.foto_local() for _ in range(2)]
        ag.publicar_agora(1, None, "Fotos", "", True, "instagram", "carrossel", [{"url": u} for u in locais])
        self.assertEqual(self.dimensoes, [(1080, 1080), (1080, 1080)])

    def test_exige_de_2_a_10_fotos(self):
        GraphMulti().aplicar(self)
        for n in (0, 1, 11):
            with self.assertRaises(ag.ErroPublicacao, msg=n):
                ag.agendar(1, None, "x", "", self.futuro, "instagram", "carrossel", self.fotos(n))
        with self.assertRaises(ValueError):  # a API recusa mais de 10 antes de chegar aqui
            app_mod.NovaPublicacao(legenda="x", midias=self.fotos(11))

    def test_agendado_guarda_midias_e_bloqueia_todos_os_itens(self):
        ids = [self.item("j1", f"P{i}", f"{10 + i},00", f"https://ml/p/p{i}0") for i in range(3)]
        p = ag.agendar(1, ids[0], "Achados", "", self.futuro, "instagram", "carrossel", self.fotos(3, ids))
        self.assertEqual((p["formato"], len(ag.listar(1)[0]["midias"])), ("carrossel", 3))
        self.assertEqual(ag.itens_elegiveis(1), [])  # os 3 itens saíram dos candidatos
        lista = ag.listar(1)[0]
        self.assertEqual([m["item_id"] for m in lista["midias"]], ids)
        self.assertEqual(lista["image_url"], tp.JPG)  # capa = primeira foto
        with self.assertRaises(ag.ErroPublicacao) as ctx:  # um deles de novo, em outro carrossel
            ag.agendar(1, ids[1], "Outro", "", self.futuro, "instagram", "carrossel", self.fotos(2, [ids[1], None]))
        self.assertEqual(ctx.exception.status, 409)
        ag.cancelar(1, p["id"])
        self.assertEqual(len(ag.itens_elegiveis(1)), 3)  # cancelou: voltam a ser candidatos

    def test_editar_carrossel_agendado(self):
        p = ag.agendar(1, None, "Antes", "", self.futuro, "instagram", "carrossel", self.fotos(2))
        novo = (datetime.now(itens.TZ_SP) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        q = ag.editar(1, p["id"], "Depois", "", novo, self.fotos(4))
        self.assertEqual(q["legenda"], "Depois")
        self.assertEqual(len(ag.listar(1)[0]["midias"]), 4)
        with self.assertRaises(ag.ErroPublicacao):
            ag.editar(1, p["id"], "Depois", "", novo, self.fotos(1))

    def test_agendador_publica_o_carrossel_vencido(self):
        g = GraphMulti().aplicar(self)
        passado = (datetime.now(itens.TZ_SP) - timedelta(minutes=1)).isoformat()
        pid = ag._inserir(1, None, "Achados", tp.JPG, "agendado", passado, "instagram", "carrossel",
                          '[{"url": "%s", "item_id": null}, {"url": "%s", "item_id": null}]' % (tp.JPG, tp.JPG))
        ag.processar_vencidos()
        self.assertEqual(ag.obter(pid)["status"], "publicado")
        self.assertEqual(len(g.medias()), 3)


class TestFormatosEmBreve(BaseCanais):
    def test_formatos_sem_publicacao_ainda_sao_recusados_com_clareza(self):
        for canal, formato in (("instagram", "reels"), ("facebook", "feed"), ("facebook", "story"),
                               ("facebook", "publicacao"), ("whatsapp", "story"), ("whatsapp", "grupo")):
            with self.assertRaises(ag.ErroPublicacao, msg=(canal, formato)) as ctx:
                ag.agendar(1, None, "x", tp.JPG, self.futuro, canal, formato)
            self.assertIn("está chegando", str(ctx.exception))
        with self.assertRaises(ag.ErroPublicacao):
            ag.agendar(1, None, "x", tp.JPG, self.futuro, "tiktok", "feed")

    def test_rota_devolve_400_para_formato_em_breve(self):
        n = app_mod.NovaPublicacao(legenda="x", image_url=tp.JPG, agendado_para=self.futuro,
                                   canal="facebook", formato="feed")
        with self.assertRaises(HTTPException) as ctx:
            app_mod.publicacao_agendar(n, u=self.u)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_publicacao_antiga_continua_como_instagram_feed(self):
        GraphMulti().aplicar(self)
        n = app_mod.NovaPublicacao(legenda="Olha", image_url=tp.JPG, agendado_para=self.futuro)
        p = app_mod.publicacao_agendar(n, u=self.u)
        self.assertEqual((p["canal"], p["formato"]), ("instagram", "feed"))
        self.assertEqual(app_mod.publicacao_listar(u=self.u)[0]["midias"], [])


class TestCanais(BaseCanais):
    def test_estado_dos_canais(self):
        r = app_mod.publicacao_canais(u=self.u)
        self.assertEqual(set(r["formatos"]), {"instagram", "facebook", "whatsapp"})
        self.assertEqual(r["formatos"]["instagram"], ["feed", "story", "carrossel", "reels"])
        self.assertIn("instagram/story", r["publicaveis"])
        self.assertNotIn("instagram/reels", r["publicaveis"])
        self.assertFalse(r["facebook"]["configurado"])


class TestImagens(unittest.TestCase):
    def test_story_e_quadrado_tem_o_tamanho_certo(self):
        for fn, tam in ((imagem_host.preparar_story, (1080, 1920)), (imagem_host.preparar_quadrado, (1080, 1080))):
            for w, h in ((600, 400), (400, 900), (1500, 1500)):
                img = Image.open(io.BytesIO(fn(jpeg_bytes(w, h))))
                self.assertEqual((img.size, img.format), (tam, "JPEG"), (fn.__name__, w, h))

    def test_arquivo_invalido(self):
        with self.assertRaises(imagem_host.ErroImagem):
            imagem_host.preparar_story(b"nao e imagem")


class FbResp:
    def __init__(self, corpo, status=200):
        self.corpo, self.status_code = corpo, status

    def json(self):
        return self.corpo


TOKEN_FB = "EAAB" + "x" * 60


class TestFacebook(tp.Base):
    def get_falso(self, resp):
        chamadas = []

        def get(url, params=None, timeout=None):
            chamadas.append((url, dict(params or {})))
            return resp
        p = mock.patch.object(facebook.requests, "get", get)
        p.start()
        self.addCleanup(p.stop)
        return chamadas

    def test_valida_pagina(self):
        ch = self.get_falso(FbResp({"id": "105736241234567", "name": "Achei Baratinho"}))
        r = facebook.validar_pagina("105736241234567", TOKEN_FB)
        self.assertEqual(r, {"id": "105736241234567", "nome": "Achei Baratinho"})
        self.assertTrue(ch[0][0].endswith("/105736241234567"))

    def test_erros_legiveis_e_sem_token(self):
        self.get_falso(FbResp({"error": {"code": 190, "message": f"bad {TOKEN_FB}"}}, 400))
        with self.assertRaises(facebook.ErroFacebook) as ctx:
            facebook.validar_pagina("105736241234567", TOKEN_FB)
        self.assertIn("expirou", str(ctx.exception))
        self.get_falso(FbResp({"error": {"code": 100, "message": "x"}}, 400))
        with self.assertRaises(facebook.ErroFacebook) as ctx:
            facebook.validar_pagina("1", TOKEN_FB)
        self.assertIn("Página", str(ctx.exception))
        self.get_falso(FbResp({"error": {"code": 1, "message": f"falhou com {TOKEN_FB}"}}, 500))
        with self.assertRaises(facebook.ErroFacebook) as ctx:
            facebook.validar_pagina("1", TOKEN_FB)
        self.assertNotIn(TOKEN_FB, str(ctx.exception))

    def test_salvar_cifra_o_token_e_a_tela_so_ve_mascarado(self):
        self.get_falso(FbResp({"id": "105736241234567", "name": "Achei Baratinho"}))
        r = tenant.facebook_salvar(1, "105736241234567", TOKEN_FB)
        self.assertTrue(r["configurado"])
        self.assertEqual(r["nome"], "Achei Baratinho")
        self.assertNotIn(TOKEN_FB, str(r))
        linha = db.conn().execute("SELECT fb_token_cifrado FROM config_usuario WHERE usuario_id=1").fetchone()
        self.assertNotIn(TOKEN_FB, linha["fb_token_cifrado"])
        self.assertEqual(cofre.abrir(linha["fb_token_cifrado"]), TOKEN_FB)
        self.assertTrue(app_mod.publicacao_canais(u=self.u)["facebook"]["configurado"])
        self.assertFalse(tenant.facebook_remover(1)["configurado"])

    def test_nao_grava_se_a_meta_recusar_ou_dados_ruins(self):
        self.get_falso(FbResp({"error": {"code": 190, "message": "x"}}, 400))
        with self.assertRaises(tenant.ErroConfig):
            tenant.facebook_salvar(1, "105736241234567", TOKEN_FB)
        for pid, tok in (("abc", TOKEN_FB), ("105736241234567", "curto"), ("105736241234567", "a b" * 20)):
            with self.assertRaises(tenant.ErroConfig):
                tenant.facebook_salvar(1, pid, tok)
        self.assertFalse(tenant.publica(1)["facebook"]["configurado"])


if __name__ == "__main__":
    unittest.main()
