"""Reels (upload e vídeo gerado das fotos) e publicação no Facebook. Graph API sempre falsa."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from tests import test_publicacao as tp
from tests.test_canais import BaseCanais, GraphMulti, jpeg_bytes
from thyfia import agente_publicacao as ag
from thyfia import app as app_mod
from thyfia import db, facebook, imagem_host, instagram, itens, tenant, video

PRECISA_FFMPEG = unittest.skipUnless(video.disponivel(), "ffmpeg não instalado")
TOKEN_FB = "EAAB" + "x" * 60


def mp4_de_teste(segundos=5, audio=True, tam="640x480") -> bytes:
    with tempfile.TemporaryDirectory() as d:
        saida = Path(d) / "t.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={tam}:rate=25:duration={segundos}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}", "-c:a", "aac", "-shortest"]
        subprocess.run([*cmd, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(saida)], check=True)
        return saida.read_bytes()


# --------------------------------------------------------------------------- vídeo (ffmpeg de verdade)
@PRECISA_FFMPEG
class TestVideo(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()

    def test_upload_vira_9x16_h264(self):
        r = video.salvar_upload(mp4_de_teste(), self.pasta)
        arq = Path(self.pasta) / Path(r["video_url"]).name
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height,codec_name", "-of", "csv=p=0", str(arq)],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(info, "h264,1080,1920")
        self.assertTrue(video.sondar(arq)["tem_audio"])
        self.assertTrue((Path(self.pasta) / Path(r["capa_url"]).name).is_file())
        self.assertEqual(imagem_host.arquivo_video(r["video_url"], self.pasta), arq)
        self.assertEqual(list(Path(self.pasta).glob("tmp*")), [])  # sem lixo

    def test_upload_sem_audio_funciona(self):
        r = video.salvar_upload(mp4_de_teste(audio=False), self.pasta)
        self.assertFalse(video.sondar(Path(self.pasta) / Path(r["video_url"]).name)["tem_audio"])

    def test_upload_recusa_o_que_nao_e_video_ou_tem_duracao_errada(self):
        for dados, trecho in ((b"", "vazio"), (b"isto nao e video", "vídeo"), (jpeg_bytes(), "vídeo"),
                              (mp4_de_teste(segundos=1, audio=False), "3 segundos")):
            with self.assertRaises(video.ErroVideo, msg=trecho) as ctx:
                video.salvar_upload(dados, self.pasta)
            self.assertIn(trecho, str(ctx.exception))
        with mock.patch.object(video, "DURACAO_MAX", 2.0), self.assertRaises(video.ErroVideo) as ctx:
            video.salvar_upload(mp4_de_teste(segundos=5, audio=False), self.pasta)
        self.assertIn("passa de", str(ctx.exception))

    def test_gera_video_das_fotos(self):
        fotos = [{"bytes": jpeg_bytes(600, 400, "red"), "titulo": "Whey Protein 900g", "preco": "89,90"},
                 {"bytes": jpeg_bytes(400, 800, "blue")}]
        r = video.gerar_de_fotos(fotos, self.pasta)
        arq = Path(self.pasta) / Path(r["video_url"]).name
        info = video.sondar(arq)
        self.assertAlmostEqual(info["duracao"], 6.0, delta=0.4)
        self.assertEqual(r["duracao"], 6.0)
        self.assertEqual(Image.open(Path(self.pasta) / Path(r["capa_url"]).name).size, (1080, 1920))
        for n in (0, 11):
            with self.assertRaises(video.ErroVideo):
                video.gerar_de_fotos([fotos[0]] * n, self.pasta)

    def test_quadro_com_faixa_mantem_tamanho(self):
        img = Image.open(io.BytesIO(video.quadro(jpeg_bytes(500, 900), "Título bem longo " * 8, "1.234,50")))
        self.assertEqual(img.size, (1080, 1920))

    def test_arquivo_video_nao_sai_da_pasta(self):
        self.assertIsNone(imagem_host.arquivo_video("/midia/../../etc/passwd.mp4", self.pasta))
        self.assertIsNone(imagem_host.arquivo_video("/midia/nao_existe.mp4", self.pasta))
        self.assertIsNone(imagem_host.arquivo_video("https://x/a.mp4", self.pasta))


# --------------------------------------------------------------------------- Reels no agente
class BaseReels(BaseCanais):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(ag, "ASSINCRONO", False)  # nos testes, "publicar agora" roda na hora
        p.start()
        self.addCleanup(p.stop)
        self.videos_publicos: list[str] = []

        def url_video(video_url, pasta):
            self.videos_publicos.append(video_url)
            return "https://publico.example/v.mp4"
        p = mock.patch.object(imagem_host, "url_video_para_instagram", url_video)
        p.start()
        self.addCleanup(p.stop)

    def video_local(self) -> str:
        nome = "teste_video.mp4"
        (Path(db.MIDIA) / nome).write_bytes(b"\x00\x00\x00 ftypmp42")
        return f"/midia/{nome}"


class TestReels(BaseReels):
    def test_publica_reels_com_container_REELS_e_espera_mais(self):
        g = GraphMulti().aplicar(self)
        p = ag.publicar_agora(1, None, "Olha só! #oferta", "", True, "instagram", "reels", video_url=self.video_local())
        self.assertEqual((p["status"], p["formato"]), ("publicado", "reels"))
        m = g.medias()[0]
        self.assertEqual((m["media_type"], m["video_url"], m["caption"], m["share_to_feed"]),
                         ("REELS", "https://publico.example/v.mp4", "Olha só! #oferta", "true"))
        self.assertNotIn("image_url", m)
        self.assertEqual(self.videos_publicos, ["/midia/teste_video.mp4"])

    def test_reels_espera_ate_5_minutos_pelo_processamento(self):
        class Lenta(GraphMulti):
            def _rota(self, metodo, url, dados):
                if metodo == "GET" and url.split("/v21.0/")[1].startswith("C"):
                    self.chamadas.append((metodo, url, dict(dados)))
                    return tp.Resp({"status_code": "IN_PROGRESS"})
                return super()._rota(metodo, url, dados)
        Lenta().aplicar(self)
        p = ag.publicar_agora(1, None, "x", "", True, "instagram", "reels", video_url=self.video_local())
        self.assertEqual(p["status"], "erro")
        self.assertIn("300s", p["erro"])
        self.assertEqual(sum(self.dormidas), 300)

    def test_reels_exige_video_e_legenda(self):
        for kw, trecho in ((dict(video_url=None), "vídeo"), (dict(video_url="/midia/nao_existe.mp4"), "vídeo"),
                           (dict(video_url="/midia/../x.mp4"), "vídeo")):
            with self.assertRaises(ag.ErroPublicacao) as ctx:
                ag.agendar(1, None, "legenda", "", self.futuro, "instagram", "reels", **kw)
            self.assertIn(trecho, str(ctx.exception))
        with self.assertRaises(ag.ErroPublicacao):
            ag.agendar(1, None, "  ", "", self.futuro, "instagram", "reels", video_url=self.video_local())

    def test_reels_nao_bloqueia_o_produto_no_feed_e_story_idem(self):
        it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        ag.agendar(1, it, "legenda", "", self.futuro, "instagram", "reels", video_url=self.video_local())
        ag.agendar(1, it, "", tp.JPG, self.futuro, "instagram", "story")
        self.assertEqual([i["id"] for i in ag.itens_elegiveis(1)], [it])
        ag.agendar(1, it, "Feed!", tp.JPG, self.futuro, "instagram", "feed")  # o feed ainda pode
        self.assertEqual(ag.itens_elegiveis(1), [])

    def test_story_de_produto_ja_publicado_no_feed_e_permitido(self):
        it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        ag.agendar(1, it, "Feed!", tp.JPG, self.futuro, "instagram", "feed")
        ag.agendar(1, it, "", tp.JPG, self.futuro, "instagram", "story")  # antes dava 409
        with self.assertRaises(ag.ErroPublicacao) as ctx:
            ag.agendar(1, it, "De novo", tp.JPG, self.futuro, "instagram", "feed")
        self.assertEqual(ctx.exception.status, 409)

    def test_agendador_publica_reels_vencido_e_editar_muda_legenda_e_hora(self):
        g = GraphMulti().aplicar(self)
        p = ag.agendar(1, None, "Antes", "", self.futuro, "instagram", "reels", video_url=self.video_local())
        novo = (datetime.now(itens.TZ_SP) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        q = ag.editar(1, p["id"], "Depois", "", novo)
        self.assertEqual((q["legenda"], q["video_url"]), ("Depois", "/midia/teste_video.mp4"))
        passado = (datetime.now(itens.TZ_SP) - timedelta(minutes=1)).isoformat()
        db.conn().execute("UPDATE publicacoes_instagram SET agendado_para=? WHERE id=?", (passado, p["id"]))
        db.conn().commit()
        ag.processar_vencidos()
        self.assertEqual(ag.obter(p["id"])["status"], "publicado")
        self.assertEqual(g.medias()[0]["caption"], "Depois")

    def test_publicar_agora_de_reels_em_segundo_plano_devolve_na_hora(self):
        import threading
        liberar = threading.Event()
        g = GraphMulti().aplicar(self)
        original = ag._executar

        def lento(pid, agendada):
            liberar.wait(5)
            original(pid, agendada)
        with mock.patch.object(ag, "ASSINCRONO", True), mock.patch.object(ag, "_executar", lento):
            p = ag.publicar_agora(1, None, "x", "", True, "instagram", "reels", video_url=self.video_local())
            self.assertEqual(p["status"], "publicando")  # voltou sem esperar o Instagram
            self.assertEqual(sum(1 for _, u, _ in g.chamadas if u.endswith("media_publish")), 0)
            liberar.set()
            for t in threading.enumerate():
                if t.name.startswith("thyfia-reels-"):
                    t.join(5)
        self.assertEqual(ag.obter(p["id"])["status"], "publicado")

    def test_publicacao_interrompida_por_reinicio_vira_erro(self):
        pid = ag._inserir(1, None, "x", "", "publicando", None, "instagram", "reels", None, "/midia/x.mp4")
        agendado = ag._inserir(1, None, "y", tp.JPG, "agendado", self.futuro)
        self.assertEqual(ag.recuperar_interrompidas(), 1)
        self.assertEqual(ag.obter(pid)["status"], "erro")
        self.assertIn("reinício", ag.obter(pid)["erro"])
        self.assertEqual(ag.obter(agendado)["status"], "agendado")

    def test_rotas_de_video(self):
        self.assertIn("instagram/reels", app_mod.publicacao_canais(u=self.u)["publicaveis"])
        n = app_mod.NovaPublicacao(legenda="x", canal="instagram", formato="reels", agendado_para=self.futuro,
                                   video_url="/midia/nao_existe.mp4")
        with self.assertRaises(HTTPException) as ctx:
            app_mod.publicacao_agendar(n, u=self.u)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_gerar_reels_rota_recusa_item_de_outro_usuario(self):
        outro = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        db.conn().execute("UPDATE jobs SET usuario_id=2 WHERE id='j1'")
        db.conn().commit()
        with self.assertRaises(HTTPException) as ctx:
            app_mod.publicacao_reels_gerar(app_mod.GerarReels(midias=[{"url": tp.JPG, "item_id": outro}]), u=self.u)
        self.assertEqual(ctx.exception.status_code, 404)

    @PRECISA_FFMPEG
    def test_gerar_reels_rota_de_ponta_a_ponta(self):
        it = self.item("j1", "Whey 900g", "89,90", "https://ml/p/aaa")
        r = app_mod.publicacao_reels_gerar(app_mod.GerarReels(midias=[{"url": tp.JPG, "item_id": it},
                                                                       {"url": tp.JPG}]), u=self.u)
        self.assertEqual(r["duracao"], 6.0)
        self.assertTrue(imagem_host.arquivo_video(r["video_url"], db.MIDIA))
        GraphMulti().aplicar(self)
        p = ag.publicar_agora(1, it, "Whey em oferta", r["capa_url"], True, "instagram", "reels", video_url=r["video_url"])
        self.assertEqual(p["status"], "publicado")
        self.assertEqual(p["image_url"], r["capa_url"])


# --------------------------------------------------------------------------- Facebook
class FbGraph:
    def __init__(self, falhar=None):
        self.chamadas: list[tuple[str, dict]] = []
        self.falhar = falhar or {}

    def post(self, url, data=None, timeout=None):
        caminho = url.split("/v21.0/")[1]
        self.chamadas.append((caminho, dict(data or {})))
        if caminho in self.falhar:
            return tp.Resp(self.falhar[caminho], 400)
        if caminho.endswith("/photos"):
            return tp.Resp({"id": "FOTO1", "post_id": "PAG_POST1"} if (data or {}).get("published") != "false" else {"id": "FOTO9"})
        if caminho.endswith("/photo_stories"):
            return tp.Resp({"success": True, "post_id": "STORY1"})
        if caminho.endswith("/feed"):
            return tp.Resp({"id": "PAG_LINK1"})
        return tp.Resp({"error": {"message": "rota?", "code": 100}}, 400)

    def get(self, url, params=None, timeout=None):
        return tp.Resp({"id": "105736241234567", "name": "Achei Baratinho"})

    def aplicar(self, teste):
        for nome in ("get", "post"):
            p = mock.patch.object(facebook.requests, nome, getattr(self, nome))
            p.start()
            teste.addCleanup(p.stop)
        return self


class TestFacebookPublicacao(BaseCanais):
    def setUp(self):
        super().setUp()
        self.fb = FbGraph().aplicar(self)
        tenant.facebook_salvar(1, "105736241234567", TOKEN_FB)

    def test_exige_pagina_conectada(self):
        tenant.facebook_remover(1)
        for fn in (lambda: ag.agendar(1, None, "x", tp.JPG, self.futuro, "facebook", "feed"),
                   lambda: ag.publicar_agora(1, None, "x", tp.JPG, True, "facebook", "feed")):
            with self.assertRaises(ag.ErroPublicacao) as ctx:
                fn()
            self.assertEqual(ctx.exception.status, 503)
            self.assertIn("Facebook", str(ctx.exception))

    def test_feed_publica_foto_com_texto(self):
        p = ag.publicar_agora(1, None, "Olha essa oferta!", tp.JPG, True, "facebook", "feed")
        self.assertEqual((p["status"], p["canal"], p["ig_media_id"]), ("publicado", "facebook", "PAG_POST1"))
        caminho, d = self.fb.chamadas[-1]
        self.assertEqual(caminho, "105736241234567/photos")
        self.assertEqual((d["message"], d["access_token"]), ("Olha essa oferta!", TOKEN_FB))
        self.assertTrue(d["url"].startswith("https://publico.example/"))

    def test_publicacao_com_link(self):
        p = ag.publicar_agora(1, None, "Veja:", "", True, "facebook", "publicacao", link="https://meli.la/abc")
        self.assertEqual(p["status"], "publicado")
        caminho, d = self.fb.chamadas[-1]
        self.assertEqual((caminho, d["link"], d["message"]), ("105736241234567/feed", "https://meli.la/abc", "Veja:"))
        for ruim in ("", "meli.la/abc", "javascript:alert(1)", "ftp://x.com/a"):
            with self.assertRaises(ag.ErroPublicacao, msg=ruim):
                ag.agendar(1, None, "Veja:", "", self.futuro, "facebook", "publicacao", link=ruim)

    def test_story_sobe_foto_nao_publicada_e_vira_story(self):
        p = ag.publicar_agora(1, None, "", tp.JPG, True, "facebook", "story")
        self.assertEqual((p["status"], p["ig_media_id"]), ("publicado", "STORY1"))
        (c1, d1), (c2, d2) = self.fb.chamadas[-2:]
        self.assertEqual((c1, d1["published"]), ("105736241234567/photos", "false"))
        self.assertEqual((c2, d2["photo_id"]), ("105736241234567/photo_stories", "FOTO9"))
        self.assertEqual(self.dimensoes, [(1080, 1920)])

    def test_erro_do_facebook_vira_mensagem_sem_token(self):
        self.fb.falhar = {"105736241234567/photos": {"error": {"code": 200, "message": f"sem permissão {TOKEN_FB}"}}}
        p = ag.publicar_agora(1, None, "x", tp.JPG, True, "facebook", "feed")
        self.assertEqual(p["status"], "erro")
        self.assertIn("pages_manage_posts", p["erro"])
        self.assertNotIn(TOKEN_FB, p["erro"])

    def test_agendador_publica_no_facebook_com_a_pagina_do_dono(self):
        passado = (datetime.now(itens.TZ_SP) - timedelta(minutes=1)).isoformat()
        pid = ag._inserir(1, None, "Agendado", tp.JPG, "agendado", passado, "facebook", "feed")
        ag.processar_vencidos()
        self.assertEqual(ag.obter(pid)["status"], "publicado")
        tenant.facebook_remover(1)
        pid2 = ag._inserir(1, None, "Sem página", tp.JPG, "agendado", passado, "facebook", "feed")
        ag.processar_vencidos()
        self.assertEqual(ag.obter(pid2)["status"], "erro")
        self.assertIn("não está conectada", ag.obter(pid2)["erro"])

    def test_candidatos_e_bloqueio_sao_por_canal(self):
        it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        ag.agendar(1, it, "No Instagram", tp.JPG, self.futuro, "instagram", "feed")
        self.assertEqual(ag.itens_elegiveis(1, canal="instagram"), [])
        self.assertEqual([i["id"] for i in ag.itens_elegiveis(1, canal="facebook")], [it])  # no Facebook ainda pode
        ag.agendar(1, it, "No Facebook", tp.JPG, self.futuro, "facebook", "feed")
        self.assertEqual(ag.itens_elegiveis(1, canal="facebook"), [])
        with self.assertRaises(ag.ErroPublicacao) as ctx:
            ag.agendar(1, it, "De novo", tp.JPG, self.futuro, "facebook", "feed")
        self.assertEqual(ctx.exception.status, 409)

    def test_editar_publicacao_com_link(self):
        p = ag.agendar(1, None, "Antes", "", self.futuro, "facebook", "publicacao", link="https://meli.la/abc")
        novo = (datetime.now(itens.TZ_SP) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        q = ag.editar(1, p["id"], "Depois", "", novo, link="https://meli.la/xyz")
        self.assertEqual((q["legenda"], q["link"]), ("Depois", "https://meli.la/xyz"))
        self.assertEqual(ag.editar(1, p["id"], "Só texto", "", novo)["link"], "https://meli.la/xyz")  # mantém o link

    def test_listar_traz_canal_video_e_link(self):
        ag.agendar(1, None, "Veja:", "", self.futuro, "facebook", "publicacao", link="https://meli.la/abc")
        r = ag.listar(1)[0]
        self.assertEqual((r["canal"], r["formato"], r["link"], r["video_url"]), ("facebook", "publicacao", "https://meli.la/abc", None))


if __name__ == "__main__":
    unittest.main()
