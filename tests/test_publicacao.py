"""Testes do Agente de Publicação (Instagram). A Graph API é sempre falsa: nada é publicado.

Rodar:  .venv/bin/python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException
from PIL import Image

from thyfia import agente_publicacao as ag
from thyfia import db, imagem_host, instagram, itens

assert "thyfia-testes-" in str(db.DB_PATH), "testes devem usar o banco temporário"

from thyfia import app as app_mod  # noqa: E402

TOKEN = os.environ["IG_ACCESS_TOKEN"]
UID_IG = os.environ["IG_USER_ID"]
JPG = "https://http2.mlstatic.com/D_NQ_NP_2X_608923-MLB120463575227_102026-F.jpg"


# --------------------------------------------------------------------------- Graph API falsa
class Resp:
    def __init__(self, corpo, status=200):
        self.corpo, self.status_code = corpo, status

    def json(self):
        if self.corpo is None:
            raise ValueError
        return self.corpo


class GraphFalsa:
    """Responde como a Graph API e registra cada chamada (método, caminho, parâmetros)."""

    def __init__(self, status_container=("FINISHED",), restante=100, erro_media=None, hosts_ok=None):
        self.chamadas: list[tuple[str, str, dict]] = []
        self.status_container = list(status_container)
        self.restante = restante
        self.erro_media = erro_media
        self.hosts_ok = hosts_ok
        self.lock = threading.Lock()

    def _rota(self, metodo, url, dados):
        with self.lock:
            self.chamadas.append((metodo, url, dict(dados)))
        host = url.split("/v21.0/")[0]
        if self.hosts_ok is not None and host not in self.hosts_ok:
            return Resp({"error": {"message": "Invalid OAuth access token - Cannot parse access token",
                                   "type": "OAuthException", "code": 190}}, 400)
        caminho = url.split("/v21.0/")[1]
        if metodo == "GET" and caminho == UID_IG:
            return Resp({"id": UID_IG, "username": "achei_baratinho_oficial"})
        if caminho.endswith("content_publishing_limit"):
            return Resp({"data": [{"quota_usage": 100 - self.restante, "config": {"quota_total": 100}}]})
        if metodo == "POST" and caminho.endswith("/media"):
            if self.erro_media:
                return Resp(self.erro_media, 400)
            return Resp({"id": "C1"})
        if metodo == "GET" and caminho == "C1":
            st = self.status_container.pop(0) if len(self.status_container) > 1 else self.status_container[0]
            return Resp({"status_code": st, "status": f"{st}: detalhe"})
        if metodo == "POST" and caminho.endswith("/media_publish"):
            return Resp({"id": "M1"})
        return Resp({"error": {"message": "rota desconhecida", "code": 100}}, 400)

    def get(self, url, params=None, timeout=None):
        return self._rota("GET", url, params or {})

    def post(self, url, data=None, timeout=None):
        return self._rota("POST", url, data or {})

    def contar(self, sufixo, metodo="POST"):
        return sum(1 for m, u, _ in self.chamadas if m == metodo and u.endswith(sufixo))

    def aplicar(self, teste):
        for nome in ("get", "post"):
            p = mock.patch.object(instagram.requests, nome, getattr(self, nome))
            p.start()
            teste.addCleanup(p.stop)
        return self


class HostFalso(imagem_host.ImageHost):
    nome = "falso"

    def __init__(self, sem_foto=()):
        self.sem_foto = set(sem_foto)

    def obter_url(self, item):
        return None if item["titulo"] in self.sem_foto else JPG


# --------------------------------------------------------------------------- base
class Base(unittest.TestCase):
    def setUp(self):
        db.init()
        c = db.conn()
        for t in ("publicacoes_instagram", "job_itens", "jobs", "usuarios"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO usuarios (id, nome, email, senha_hash, sal) VALUES (1,'T','t@t','x','y')")
        c.execute("INSERT INTO usuarios (id, nome, email, senha_hash, sal) VALUES (2,'O','o@o','x','y')")
        c.commit()
        self.u = {"id": 1}
        instagram._host_ativo = None
        app_mod._cache_ig.update(em=0, dados=None)
        p = mock.patch.object(imagem_host, "validar_jpeg", lambda url: url.startswith("https://"))
        p.start()
        self.addCleanup(p.stop)
        self.dormidas: list[float] = []
        p2 = mock.patch.object(instagram.time, "sleep", self.dormidas.append)
        p2.start()
        self.addCleanup(p2.stop)

    def job(self, job_id="j1", termo="carregador", uid=1):
        c = db.conn()
        c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, status) VALUES (?,?,?,20,'concluido')",
                  (job_id, uid, termo))
        c.commit()

    def item(self, job_id, titulo, preco, url, link="auto", criado="2026-11-20T10:00:00-03:00",
             busca="carregador", imagem=JPG) -> int:
        itens.salvar_item(job_id, busca, titulo, preco, url,
                          f"https://meli.la/{url[-3:]}" if link == "auto" else link,
                          criado_em=criado, imagem=imagem)
        return db.conn().execute("SELECT MAX(id) FROM job_itens").fetchone()[0]

    def pub(self, item_id, status, quando=None, uid=1) -> int:
        return ag._inserir(uid, item_id, "legenda", JPG, status, quando)


# --------------------------------------------------------------------------- ranking / legenda
class TestRanking(Base):
    def test_menor_preco_e_desempate_pelo_mais_recente(self):
        dados = [
            {"id": 1, "preco": 30.0, "criado_em": "2026-11-20T10:00:00-03:00", "titulo": "A"},
            {"id": 2, "preco": 19.9, "criado_em": "2026-11-19T10:00:00-03:00", "titulo": "B antigo"},
            {"id": 3, "preco": 19.9, "criado_em": "2026-11-21T10:00:00-03:00", "titulo": "B novo"},
        ]
        r = ag.ranking_menor_preco(dados, "carregador")
        self.assertEqual([i["titulo"] for i in r], ["B novo", "B antigo", "A"])
        self.assertIn("Menor preço entre 3 itens de “carregador”: R$ 19,90", r[0]["motivo"])
        self.assertIn("mais recente", r[0]["motivo"])
        self.assertTrue(r[2]["motivo"].startswith("3º menor preço"))
        self.assertIs(ag.ESTRATEGIA, ag.ranking_menor_preco)  # ponto único para trocar a regra

    def test_legenda(self):
        it = {"id": 7, "titulo": "Carregador Turbo 20W", "preco": 1234.56, "busca": "carregador",
              "link_afiliado": "https://meli.la/xyz"}
        leg = ag.gerar_legenda(it, "Carregador Rápido")
        self.assertIn("Carregador Turbo 20W", leg)
        self.assertIn("Por R$ 1.234,56", leg)
        self.assertIn("Link na bio e no nosso canal do Telegram/WhatsApp", leg)
        self.assertNotIn("meli.la", leg)  # link de afiliado nunca na legenda
        tags = [p for p in leg.split() if p.startswith("#")]
        self.assertEqual(len(tags), 5)
        self.assertIn("#carregadorrapido", tags)
        self.assertEqual(len(set(tags)), 5)


class TestCandidatos(Base):
    def test_filtra_publicados_agendados_e_mesmo_produto(self):
        self.job("j1"); self.job("j2")
        a = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        b = self.item("j1", "B", "20,00", "https://ml/p/bbb")
        self.item("j2", "A outra geração", "9,00", "https://ml/p/aaa", link="https://meli.la/zzz")
        c = self.item("j1", "C", "30,00", "https://ml/p/ccc")
        d = self.item("j1", "D", "40,00", "https://ml/p/ddd")
        self.item("j1", "Sem link", "1,00", "https://ml/p/eee", link=None)
        self.item("j1", "Sem preço", None, "https://ml/p/fff")
        self.pub(a, "publicado")
        self.pub(b, "agendado", "2026-12-01T10:00:00-03:00")
        self.pub(c, "erro")        # erro e cancelado voltam a ser candidatos
        self.pub(d, "cancelado")
        titulos = [i["titulo"] for i in ag.itens_elegiveis(1)]
        self.assertEqual(sorted(titulos), ["C", "D"])

    def test_sugerir_top_n_com_motivo_legenda_e_sem_foto(self):
        self.job("j1", "carregador"); self.job("j2", "fone")
        self.item("j1", "Caro", "50,00", "https://ml/p/001")
        self.item("j1", "Barato sem foto", "5,00", "https://ml/p/002", imagem=None)
        self.item("j1", "Médio", "15,00", "https://ml/p/003")
        self.item("j2", "Fone", "1,00", "https://ml/p/004", busca="fone", criado="2026-11-21T10:00:00-03:00")
        self.item("j1", "Outro usuário?", "1,00", "https://ml/p/005")
        db.conn().execute("UPDATE jobs SET usuario_id=2 WHERE id='j2'")
        db.conn().commit()
        r = ag.sugerir(1, "carregador", quantidade=2, host=HostFalso(sem_foto={"Barato sem foto"}))
        self.assertEqual(r["keyword"], "carregador")
        self.assertEqual([c["titulo"] for c in r["candidatos"]], ["Outro usuário?", "Barato sem foto"])
        self.assertTrue(r["candidatos"][1]["precisa_foto"])
        self.assertEqual(r["candidatos"][1]["image_url"], "")
        self.assertEqual(r["candidatos"][0]["image_url"], JPG)
        self.assertIn("Menor preço", r["candidatos"][0]["motivo"])
        self.assertIn("Por R$ 1,00", r["candidatos"][0]["legenda"])
        self.assertNotIn("fone", [p.lower() for p in r["palavras"]])  # item de outro usuário não aparece

    def test_sem_keyword_usa_a_busca_mais_recente(self):
        self.job("j1", "carregador"); self.job("j2", "fone")
        self.item("j1", "Carregador", "5,00", "https://ml/p/001")
        self.item("j2", "Fone", "9,00", "https://ml/p/002", busca="fone")
        r = ag.sugerir(1, host=HostFalso())
        self.assertEqual(r["keyword"], "fone")
        self.assertEqual([c["titulo"] for c in r["candidatos"]], ["Fone"])


# --------------------------------------------------------------------------- Graph API
class TestInstagram(Base):
    def test_fluxo_container_poll_publish(self):
        g = GraphFalsa(status_container=("IN_PROGRESS", "IN_PROGRESS", "FINISHED")).aplicar(self)
        self.assertEqual(instagram.publicar_imagem(JPG, "legenda"), "M1")
        media = [d for m, u, d in g.chamadas if u.endswith(f"{UID_IG}/media")][0]
        self.assertEqual((media["image_url"], media["caption"]), (JPG, "legenda"))
        self.assertEqual(g.contar("/C1", "GET"), 3)
        self.assertEqual(self.dormidas, [3, 3])  # poll a cada 3s
        pub = [d for m, u, d in g.chamadas if u.endswith("media_publish")][0]
        self.assertEqual(pub["creation_id"], "C1")

    def test_container_error_e_timeout(self):
        GraphFalsa(status_container=("ERROR",)).aplicar(self)
        with self.assertRaises(instagram.ErroInstagram) as ctx:
            instagram.publicar_imagem(JPG, "x")
        self.assertIn("ERROR", str(ctx.exception))
        g = GraphFalsa(status_container=("IN_PROGRESS",)).aplicar(self)
        with self.assertRaises(instagram.ErroInstagram) as ctx:
            instagram.aguardar_container("C1")
        self.assertIn("60s", str(ctx.exception))
        self.assertEqual(sum(self.dormidas), 60)  # 20 consultas de 3s, nada além de 60s
        self.assertEqual(g.contar("media_publish"), 0)

    def test_erro_da_api_legivel(self):
        GraphFalsa(erro_media={"error": {"message": "Only photo or video can be accepted as media type.",
                                         "code": 9004, "error_subcode": 2207052,
                                         "error_user_msg": "A mídia não pôde ser buscada neste URI"}}).aplicar(self)
        with self.assertRaises(instagram.ErroInstagram) as ctx:
            instagram.criar_container(JPG, "x")
        e = ctx.exception
        self.assertEqual(e.codigo, "imagem")
        self.assertIn("A mídia não pôde ser buscada", str(e))
        self.assertIn("código 9004/2207052", str(e))

    def test_validar_conta_tenta_graph_instagram(self):
        g = GraphFalsa(hosts_ok={instagram.HOST_INSTAGRAM}).aplicar(self)
        conta = instagram.validar_conta()
        self.assertEqual(conta["username"], "achei_baratinho_oficial")
        self.assertEqual(conta["host"], instagram.HOST_INSTAGRAM)
        self.assertFalse(conta["host_padrao"])
        instagram.limite_publicacao()  # as próximas chamadas usam o host que funcionou
        self.assertTrue(g.chamadas[-1][1].startswith(instagram.HOST_INSTAGRAM))
        self.assertEqual(g.contar(UID_IG, "GET"), 2)  # validar_conta só lê; nada de POST
        self.assertFalse(any(m == "POST" for m, _, _ in g.chamadas))

    def test_nao_configurado(self):
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": ""}):
            self.assertFalse(instagram.configurado())
            st = app_mod.instagram_status(u=self.u)
            self.assertFalse(st["configurado"])
            self.assertEqual(st["mensagem"], "Instagram não configurado")
            with self.assertRaises(ag.ErroPublicacao) as ctx:
                ag.publicar_agora(1, None, "x", JPG, confirmar=True)
            self.assertEqual(ctx.exception.status, 503)
            with self.assertRaises(instagram.ErroInstagram) as ctx2:
                instagram.validar_conta()
            self.assertEqual(ctx2.exception.codigo, "config")
        self.assertTrue(instagram.configurado())


# --------------------------------------------------------------------------- agendador
class TestAgendador(Base):
    def setUp(self):
        super().setUp()
        self.job("j1")
        self.it = self.item("j1", "A", "10,00", "https://ml/p/aaa")

    def test_vencido_publica_uma_vez_so(self):
        g = GraphFalsa().aplicar(self)
        passado = (datetime.now(itens.TZ_SP) - timedelta(minutes=1)).isoformat()
        futuro = (datetime.now(itens.TZ_SP) + timedelta(hours=1)).isoformat()
        p1 = self.pub(self.it, "agendado", passado)
        it2 = self.item("j1", "B", "20,00", "https://ml/p/bbb")
        p2 = self.pub(it2, "agendado", futuro)
        # duas threads ao mesmo tempo + mais um ciclo depois: sai uma vez só
        ts = [threading.Thread(target=ag.processar_vencidos) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        ag.processar_vencidos()
        self.assertEqual(g.contar("media_publish"), 1)
        self.assertEqual(ag.obter(p1)["status"], "publicado")
        self.assertEqual(ag.obter(p1)["ig_media_id"], "M1")
        self.assertEqual(ag.obter(p2)["status"], "agendado")  # ainda não venceu

    def test_respeita_limite_de_publicacao(self):
        g = GraphFalsa(restante=0).aplicar(self)
        p = self.pub(self.it, "agendado", "2020-01-01T00:00:00-03:00")
        ag.processar_vencidos()
        self.assertEqual(g.contar("/media"), 0)
        self.assertEqual(ag.obter(p)["status"], "agendado")  # volta para a fila
        self.assertIn("Limite", ag.obter(p)["erro"])

    def test_erro_da_api_marca_erro(self):
        GraphFalsa(status_container=("EXPIRED",)).aplicar(self)
        p = self.pub(self.it, "agendado", "2020-01-01T00:00:00-03:00")
        ag.processar_vencidos()
        self.assertEqual(ag.obter(p)["status"], "erro")
        self.assertIn("EXPIRED", ag.obter(p)["erro"])

    def test_reinicio_nao_republica_o_que_estava_publicando(self):
        g = GraphFalsa().aplicar(self)
        p = self.pub(self.it, "publicando", "2020-01-01T00:00:00-03:00")
        db.init()  # simula o reinício do servidor
        self.assertEqual(ag.obter(p)["status"], "erro")
        self.assertIn("Confira no Instagram", ag.obter(p)["erro"])
        ag.processar_vencidos()
        self.assertEqual(g.chamadas, [])

    def test_agendador_desligado_nos_testes(self):
        self.assertIsNone(ag.iniciar_agendador())


# --------------------------------------------------------------------------- rotas / regras
class TestRotas(Base):
    def setUp(self):
        super().setUp()
        self.job("j1")
        self.it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        self.futuro = (datetime.now(itens.TZ_SP) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

    def nova(self, **k):
        base = {"item_id": self.it, "legenda": "Olha esse preço!", "image_url": JPG, "agendado_para": self.futuro}
        return app_mod.NovaPublicacao(**(base | k))

    def test_agendar_listar_cancelar(self):
        GraphFalsa().aplicar(self)
        p = app_mod.publicacao_agendar(self.nova(), u=self.u)
        self.assertEqual(p["status"], "agendado")
        self.assertTrue(p["agendado_para"].endswith("-03:00"))  # horário sem fuso = São Paulo
        with self.assertRaises(HTTPException) as ctx:  # mesmo item duas vezes
            app_mod.publicacao_agendar(self.nova(), u=self.u)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual([x["status"] for x in app_mod.publicacao_listar(u=self.u)], ["agendado"])
        self.assertEqual(app_mod.publicacao_cancelar(p["id"], u=self.u)["status"], "cancelado")
        with self.assertRaises(HTTPException) as ctx:
            app_mod.publicacao_cancelar(p["id"], u=self.u)
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException) as ctx:  # de outro usuário
            app_mod.publicacao_cancelar(p["id"], u={"id": 2})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_editar_agendada(self):
        GraphFalsa().aplicar(self)
        p = app_mod.publicacao_agendar(self.nova(), u=self.u)
        novo = (datetime.now(itens.TZ_SP) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        foto2 = JPG.replace("-F.jpg", "-O.jpg")
        e = app_mod.publicacao_editar(p["id"], self.nova(legenda="Nova legenda", image_url=foto2,
                                                         agendado_para=novo), u=self.u)
        self.assertEqual((e["status"], e["legenda"], e["image_url"]), ("agendado", "Nova legenda", foto2))
        self.assertTrue(e["agendado_para"].startswith(novo))
        self.assertEqual(e["item_id"], self.it)  # continua ligada ao mesmo item
        for k, cod in (({"agendado_para": "2020-01-01T10:00"}, 400), ({"legenda": ""}, 400)):
            with self.assertRaises(HTTPException) as ctx:
                app_mod.publicacao_editar(p["id"], self.nova(**k), u=self.u)
            self.assertEqual(ctx.exception.status_code, cod)
        with self.assertRaises(HTTPException) as ctx:  # de outro usuário
            app_mod.publicacao_editar(p["id"], self.nova(), u={"id": 2})
        self.assertEqual(ctx.exception.status_code, 404)
        app_mod.publicacao_cancelar(p["id"], u=self.u)
        with self.assertRaises(HTTPException) as ctx:  # só agendada pode ser alterada
            app_mod.publicacao_editar(p["id"], self.nova(), u=self.u)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ag.obter(p["id"])["legenda"], "Nova legenda")

    def test_editar_com_foto_enviada_e_horario_vencido_publica_a_nova_versao(self):
        g = GraphFalsa().aplicar(self)
        p = app_mod.publicacao_agendar(self.nova(), u=self.u)
        buf = io.BytesIO()
        Image.new("RGB", (800, 1000), "blue").save(buf, "JPEG")
        up = imagem_host.salvar_upload(buf.getvalue(), db.MIDIA)
        app_mod.publicacao_editar(p["id"], self.nova(legenda="Editada", image_url=up["image_url"]), u=self.u)
        db.conn().execute("UPDATE publicacoes_instagram SET agendado_para='2020-01-01T00:00:00-03:00' WHERE id=?", (p["id"],))
        db.conn().commit()
        temp = "https://litter.catbox.moe/zzz.jpg"
        def post(url, *a, **k):
            return mock.Mock(status_code=200, text=temp) if url == imagem_host.HOST_TEMP_URL else g.post(url, data=k.get("data"))
        with mock.patch.object(imagem_host.requests, "post", side_effect=post):
            ag.processar_vencidos()
        media = [d for m, u, d in g.chamadas if u.endswith(f"{UID_IG}/media")][0]
        self.assertEqual((media["caption"], media["image_url"]), ("Editada", temp))
        self.assertEqual(ag.obter(p["id"])["status"], "publicado")

    def test_agendar_no_passado_ou_sem_foto(self):
        for k, msg in (({"agendado_para": "2020-01-01T10:00"}, "futuro"), ({"image_url": ""}, "foto"),
                       ({"image_url": "http://127.0.0.1:8000/x.jpg"}, "pública"), ({"legenda": " "}, "legenda")):
            with self.assertRaises(HTTPException) as ctx:
                app_mod.publicacao_agendar(self.nova(**k), u=self.u)
            self.assertIn(msg, ctx.exception.detail)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_publicar_agora_exige_confirmacao(self):
        g = GraphFalsa().aplicar(self)
        with self.assertRaises(HTTPException) as ctx:
            app_mod.publicacao_publicar_agora(self.nova(confirmar=False), u=self.u)
        self.assertIn("Confirme", ctx.exception.detail)
        self.assertEqual(g.chamadas, [])
        p = app_mod.publicacao_publicar_agora(self.nova(confirmar=True), u=self.u)
        self.assertEqual((p["status"], p["ig_media_id"]), ("publicado", "M1"))
        self.assertEqual(ag.itens_elegiveis(1), [])  # já publicado não volta a ser candidato

    def test_post_manual_com_foto_enviada(self):
        buf = io.BytesIO()
        Image.new("RGBA", (400, 1200), (255, 0, 0, 255)).save(buf, "PNG")  # PNG alto demais (1:3)
        r = imagem_host.salvar_upload(buf.getvalue(), db.MIDIA)
        self.assertFalse(r["publica"])
        self.assertEqual(r["image_url"], r["local_url"])  # fica local até a hora de publicar
        self.assertIn("link temporário", r["aviso"])
        arq = db.MIDIA / r["arquivo"]
        self.assertEqual(arq.read_bytes()[:3], b"\xff\xd8\xff")  # virou JPEG
        with Image.open(arq) as im:
            w, h = im.size
        self.assertAlmostEqual(w / h, 4 / 5, places=2)  # margem para caber no feed
        # publicar agora com a foto local: sobe para o link temporário e o Instagram recebe essa URL
        g = GraphFalsa().aplicar(self)
        temp = "https://litter.catbox.moe/abc123.jpg"
        def rota(resposta):  # o módulo requests é o mesmo: só o link temporário é simulado aqui
            def post(url, *a, **k):
                return resposta if url == imagem_host.HOST_TEMP_URL else g.post(url, data=k.get("data"))
            return mock.Mock(side_effect=post)
        up = rota(mock.Mock(status_code=200, text=temp + "\n"))
        with mock.patch.object(imagem_host.requests, "post", up):
            p = app_mod.publicacao_publicar_agora(self.nova(item_id=None, image_url=r["image_url"],
                                                            legenda="Minha legenda", confirmar=True), u=self.u)
        self.assertEqual(p["status"], "publicado")
        self.assertEqual(p["image_url"], r["local_url"])  # o histórico continua mostrando a foto local
        self.assertEqual([c.args[0] for c in up.call_args_list].count(imagem_host.HOST_TEMP_URL), 1)
        media = [d for m, u, d in g.chamadas if u.endswith(f"{UID_IG}/media")][0]
        self.assertEqual(media["image_url"], temp)
        # agendado com foto local: o link só é criado no horário (não expira antes)
        p2 = app_mod.publicacao_agendar(self.nova(item_id=None, image_url=r["image_url"]), u=self.u)
        self.assertEqual(p2["image_url"], r["local_url"])
        # serviço temporário fora do ar → erro legível, nada publicado
        db.conn().execute("UPDATE publicacoes_instagram SET agendado_para='2020-01-01T00:00:00-03:00' WHERE id=?", (p2["id"],))
        db.conn().commit()
        with mock.patch.object(imagem_host.requests, "post", rota(mock.Mock(status_code=503, text="x"))):
            ag.processar_vencidos()
        self.assertEqual(ag.obter(p2["id"])["status"], "erro")
        self.assertIn("link temporário", ag.obter(p2["id"])["erro"])
        self.assertEqual(g.contar("media_publish"), 1)
        # foto que não existe / caminho fora da pasta
        for ruim in ("/midia/naoexiste.jpg", "/midia/../thyfia.db"):
            with self.assertRaises(HTTPException):
                app_mod.publicacao_agendar(self.nova(item_id=None, image_url=ruim), u=self.u)
        with mock.patch.dict(os.environ, {"THYFIA_URL_PUBLICA": "https://thyfia.exemplo.com"}):
            r2 = imagem_host.salvar_upload(buf.getvalue(), db.MIDIA)
        self.assertTrue(r2["image_url"].startswith("https://thyfia.exemplo.com/midia/"))
        p = app_mod.publicacao_agendar(self.nova(item_id=None, image_url=r2["image_url"],
                                                 legenda="Minha legenda livre"), u=self.u)
        self.assertEqual((p["item_id"], p["legenda"]), (None, "Minha legenda livre"))
        with self.assertRaises(imagem_host.ErroImagem):
            imagem_host.salvar_upload(b"nao sou imagem", db.MIDIA)

    def test_para_jpg_e_url_publica(self):
        self.assertEqual(imagem_host.para_jpg("https://x/a-O.webp"), "https://x/a-O.jpg")
        self.assertEqual(imagem_host.para_jpg("https://x/a.webp?x=1"), "https://x/a.jpg?x=1")
        self.assertFalse(imagem_host.url_publica("https://localhost/a.jpg"))
        self.assertFalse(imagem_host.url_publica("https://192.168.0.2/a.jpg"))
        self.assertFalse(imagem_host.url_publica("http://exemplo.com/a.jpg"))
        self.assertTrue(imagem_host.url_publica(JPG))


class TestValidarJpeg(unittest.TestCase):
    def test_head_405_cai_para_get(self):
        class R:
            def __init__(self, st, tipo, corpo=b""):
                self.status_code, self.headers, self.corpo = st, {"Content-Type": tipo}, corpo

            def iter_content(self, n):
                yield self.corpo[:n]

            def close(self):
                pass
        with mock.patch.object(imagem_host.requests, "head", return_value=R(405, "text/html")), \
                mock.patch.object(imagem_host.requests, "get", return_value=R(200, "image/jpeg", b"\xff\xd8\xff\xe0")):
            self.assertTrue(imagem_host.validar_jpeg(JPG))
        with mock.patch.object(imagem_host.requests, "head", return_value=R(405, "text/html")), \
                mock.patch.object(imagem_host.requests, "get", return_value=R(200, "image/gif", b"GIF89")):
            self.assertFalse(imagem_host.validar_jpeg(JPG))  # placeholder do CDN não serve
        with mock.patch.object(imagem_host.requests, "head", return_value=R(200, "image/jpeg")):
            self.assertTrue(imagem_host.validar_jpeg(JPG))


# --------------------------------------------------------------------------- token nunca vaza
class TestToken(Base):
    def test_token_fora_de_logs_erros_e_respostas(self):
        self.job("j1")
        it = self.item("j1", "A", "10,00", "https://ml/p/aaa")
        # a API devolve uma mensagem que ecoa o token e a URL com access_token
        eco = {"error": {"message": f"Bad token {TOKEN} em https://graph.facebook.com/x?access_token={TOKEN}&a=1",
                         "code": 190}}
        GraphFalsa(erro_media=eco).aplicar(self)
        p = self.pub(it, "agendado", "2020-01-01T00:00:00-03:00")
        with self.assertLogs("thyfia", level="DEBUG") as logs:
            logging.getLogger("thyfia").debug("início")
            ag.processar_vencidos()
        tudo = "\n".join(logs.output)
        pub = ag.obter(p)
        self.assertEqual(pub["status"], "erro")
        self.assertIn("token", pub["erro"].lower())
        for texto in (tudo, pub["erro"], json.dumps(app_mod.publicacao_listar(u=self.u))):
            self.assertNotIn(TOKEN, texto)
        # falha de rede cujo texto inclui a URL com o token
        exc = instagram.requests.ConnectionError(f"Max retries: /media?access_token={TOKEN}")
        with mock.patch.object(instagram.requests, "get", side_effect=exc):
            with self.assertRaises(instagram.ErroInstagram) as ctx:
                instagram.limite_publicacao()
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertNotIn(TOKEN, repr(ctx.exception.__context__) if ctx.exception.__suppress_context__ is False else "")
        # rotas de status/validação
        GraphFalsa().aplicar(self)
        st = app_mod.instagram_status(atualizar=True, u=self.u)
        self.assertEqual(st["username"], "achei_baratinho_oficial")
        self.assertNotIn(TOKEN, json.dumps(st))
        self.assertNotIn(TOKEN, json.dumps(app_mod.instagram_validar(u=self.u)))

    def test_token_vai_no_corpo_do_post_e_nao_na_url(self):
        g = GraphFalsa().aplicar(self)
        instagram.criar_container(JPG, "x")
        metodo, url, dados = g.chamadas[-1]
        self.assertNotIn(TOKEN, url)
        self.assertEqual(dados["access_token"], TOKEN)


if __name__ == "__main__":
    unittest.main()


class TestFotoNaBusca(unittest.TestCase):
    def test_polycard_traz_a_foto_em_jpeg(self):
        from thyfia import motor_busca
        html = ('"polycard":{"metadata":{"id":"MLB123","url":"www.mercadolivre.com.br/carregador/p/MLB123"},'
                '"pictures":{"pictures":[{"id":"608923-MLB120463575227_102026"}]},'
                '"components":[{"title":{"text":"Carregador"}},{"price":{"prc":19.9}}]}')
        p = motor_busca.extrair_produtos(html)[0]
        self.assertEqual(p["imagem"], JPG)
        sem = motor_busca.extrair_produtos(html.replace("608923-MLB120463575227_102026", "x"))[0]
        self.assertIsNone(sem["imagem"])
