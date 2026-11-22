"""Piloto Automático: curadoria do Cérebro, destinos com código, fila anti-bloqueio, aprovação e relatório.
Nenhum envio é real: WhatsApp/Telegram são substituídos por um gravador."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException

from tests import test_publicacao as tp
from thyfia import app as app_mod
from thyfia import cerebro, configuracoes, db, envio_canais, mensagens, piloto, planos
from thyfia.envio_canais import ErroEnvio
from thyfia.itens import TZ_SP

AGORA = datetime(2026, 11, 22, 10, 0, tzinfo=TZ_SP)
GRUPO = "120363000000000001@g.us"
CANAL = "120363000000000002@newsletter"


class Gravador:
    """Substitui o envio real: guarda tudo e pode falhar sob demanda."""

    def __init__(self):
        self.whats: list[tuple[str, str, str | None]] = []
        self.telegram: list[tuple[str, str, str | None, str]] = []
        self.texto_tg: list[tuple[str, str]] = []
        self.falha_whats: ErroEnvio | None = None
        self.falha_tg: ErroEnvio | None = None
        self.ordem: list[str] = []  # tudo o que foi enviado, na ordem
        self.instancias: list[str | None] = []  # instância usada em cada envio de WhatsApp

    def whatsapp_enviar(self, jid, texto, imagem=None, instancia=None):
        if self.falha_whats:
            raise self.falha_whats
        self.whats.append((jid, texto, imagem))
        self.instancias.append(instancia)
        self.ordem.append(texto)

    def telegram_enviar(self, chat, html, imagem, link):
        if self.falha_tg:
            raise self.falha_tg
        self.telegram.append((chat, html, imagem, link))

    def telegram_texto(self, chat, texto):
        if self.falha_tg:
            raise self.falha_tg
        self.texto_tg.append((chat, texto))
        self.ordem.append(texto)

    def whatsapp_texto_privado(self, numero, texto, instancia=None):
        self.whats.append((f"privado:{numero}", texto, None))
        self.instancias.append(instancia)

    def aplicar(self, teste):
        for nome in ("whatsapp_enviar", "telegram_enviar", "telegram_texto", "whatsapp_texto_privado"):
            p = mock.patch.object(envio_canais, nome, getattr(self, nome))
            p.start()
            teste.addCleanup(p.stop)
        return self

    def codigo(self) -> str:
        """O código de confirmação que chegou no destino (como o cliente o leria)."""
        for t in reversed(self.ordem):
            if "código de confirmação" in t:
                return t.split("é ")[1][:4]
        raise AssertionError("nenhum código enviado")


class Base(tp.Base):
    def setUp(self):
        super().setUp()
        c = db.conn()
        for t in ("piloto_envios", "piloto_destinos", "piloto_config", "assinaturas", "config_usuario",
                  "produtos", "termos"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
        self.g = Gravador().aplicar(self)
        for nome in ("whatsapp_resolver_convite",):
            p = mock.patch.object(envio_canais, nome, lambda e: {"jid": GRUPO, "nome": "Achadinhos VIP"})
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(piloto, "GAP_GLOBAL_S", 0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(piloto, "ESPACO_DESTINOS_S", (10, 10))
        p.start()
        self.addCleanup(p.stop)
        piloto._ultimo_wa.clear()
        self.host = tp.HostFalso()
        self.job("j1", "carregador")

    def oferta(self, titulo, preco, n, busca="carregador", criado="2026-11-21T10:00:00-03:00", **kw):
        return self.item("j1", titulo, preco, f"https://produto.mercadolivre.com.br/MLB-{n:03d}", busca=busca,
                         criado=criado, **kw)

    def destino(self, tipo="whatsapp_grupo", entrada=GRUPO, uid=1) -> dict:
        d = piloto.adicionar_destino(uid, tipo, entrada if tipo != "whatsapp_grupo" else "https://chat.whatsapp.com/ABCDEFGHIJKLMNOP")
        return piloto.confirmar_destino(uid, d["id"], self.g.codigo())

    def ligar(self, **cfg):
        return piloto.salvar_config(1, {"ativo": True, **cfg})

    def ciclo(self, agora=AGORA, forcar=False):
        r = piloto.planejar(1, agora, forcar, self.host)
        return r


class TestConfig(Base):
    def test_valida_e_limita_valores(self):
        c = piloto.salvar_config(1, {"intervalo_min": 1, "max_dia": 999, "hora_ini": 8, "hora_fim": 21})
        self.assertEqual(c["intervalo_min"], 10)  # nunca mais rápido que 10 min (anti-bloqueio)
        self.assertEqual(c["max_dia"], 60)
        with self.assertRaises(piloto.ErroPiloto):
            piloto.salvar_config(1, {"hora_ini": 20, "hora_fim": 9})
        with self.assertRaises(piloto.ErroPiloto):
            piloto.salvar_config(1, {"modo": "maluco"})
        with self.assertRaises(piloto.ErroPiloto):
            piloto.salvar_config(1, {"preco_min": 50, "preco_max": 10})

    def test_palavras_sao_limpas(self):
        c = piloto.salvar_config(1, {"palavras": ["  fone   bluetooth ", "", "tênis"]})
        self.assertEqual(c["palavras"], ["fone bluetooth", "tênis"])


class TestDestinos(Base):
    def test_codigo_chega_no_destino_e_confirma(self):
        d = piloto.adicionar_destino(1, "whatsapp_grupo", "https://chat.whatsapp.com/ABCDEFGHIJKLMNOP")
        self.assertFalse(d["verificado"])
        self.assertEqual(d["nome"], "Achadinhos VIP")
        self.assertEqual(self.g.whats[-1][0], GRUPO)
        with self.assertRaises(piloto.ErroPiloto):
            piloto.confirmar_destino(1, d["id"], "0000" if self.g.codigo() != "0000" else "1111")
        ok = piloto.confirmar_destino(1, d["id"], self.g.codigo())
        self.assertTrue(ok["verificado"])

    def test_bloqueia_depois_de_5_tentativas(self):
        d = piloto.adicionar_destino(1, "whatsapp_grupo", GRUPO)
        errado = "9999" if self.g.codigo() != "9999" else "8888"
        for _ in range(5):
            with self.assertRaises(piloto.ErroPiloto):
                piloto.confirmar_destino(1, d["id"], errado)
        with self.assertRaises(piloto.ErroPiloto) as cm:
            piloto.confirmar_destino(1, d["id"], self.g.codigo())
        self.assertEqual(cm.exception.status, 429)

    def test_numero_fora_do_grupo_explica_o_que_fazer(self):
        configuracoes.definir("evolution_numero", "5534999990000")
        self.g.falha_whats = ErroEnvio("recusou", "destino")
        with self.assertRaises(piloto.ErroPiloto) as cm:
            piloto.adicionar_destino(1, "whatsapp_grupo", GRUPO)
        self.assertIn("5534999990000", str(cm.exception))
        self.assertEqual(piloto.listar_destinos(1), [])  # nada fica pela metade

    def test_outro_cliente_nao_confirma_destino_alheio(self):
        d = piloto.adicionar_destino(1, "whatsapp_grupo", GRUPO)
        with self.assertRaises(piloto.ErroPiloto) as cm:
            piloto.confirmar_destino(2, d["id"], self.g.codigo())
        self.assertEqual(cm.exception.status, 404)

    def test_canal_e_telegram_e_limite(self):
        c = self.destino("whatsapp_canal", CANAL)
        self.assertTrue(c["experimental"])
        t = piloto.adicionar_destino(1, "telegram", "https://t.me/minhasofertas")
        self.assertEqual(t["ref"], "@minhasofertas")
        self.assertEqual(self.g.texto_tg[-1][0], "@minhasofertas")
        with self.assertRaises(piloto.ErroPiloto):
            piloto.adicionar_destino(1, "whatsapp_canal", "canal-sem-formato")
        with self.assertRaises(piloto.ErroPiloto):
            piloto.adicionar_destino(1, "whatsapp_grupo", "")


class TestCuradoria(Base):
    def test_descarta_velhas_e_fora_do_filtro(self):
        self.oferta("Fone novo", 100, 1)
        self.oferta("Fone velho", 80, 2, criado="2026-11-01T10:00:00-03:00")  # > validade
        self.oferta("Fone caro", 900, 3)
        piloto.salvar_config(1, {"preco_max": 500})
        titulos = [o["titulo"] for o in piloto.candidatos(1, piloto.config(1), AGORA)]
        self.assertEqual(titulos, ["Fone novo"])

    def test_filtra_por_palavras_chave(self):
        self.oferta("Fone", 100, 1, busca="fone bluetooth")
        self.oferta("Tênis", 100, 2, busca="tenis corrida")
        piloto.salvar_config(1, {"palavras": ["tênis"]})
        self.assertEqual([o["titulo"] for o in piloto.candidatos(1, piloto.config(1), AGORA)], ["Tênis"])

    def test_cerebro_descarta_produto_que_a_comunidade_viu_falhar(self):
        self.oferta("Produto ruim", 50, 1)
        self.oferta("Produto bom", 90, 2)
        db.conn().execute("INSERT INTO produtos (url, titulo, falhas, gerados) VALUES (?,?,3,0)",
                          ("https://produto.mercadolivre.com.br/MLB-001", "Produto ruim"))
        db.conn().commit()
        self.assertEqual([o["titulo"] for o in piloto.candidatos(1, piloto.config(1), AGORA)], ["Produto bom"])

    def test_cerebro_premia_link_validado_e_explica(self):
        self.oferta("Comum", 100, 1)
        self.oferta("Validado", 100, 2)
        db.conn().execute("INSERT INTO produtos (url, titulo, falhas, gerados) VALUES (?,?,0,6)",
                          ("https://produto.mercadolivre.com.br/MLB-002", "Validado"))
        db.conn().commit()
        top = piloto.candidatos(1, piloto.config(1), AGORA)[0]
        self.assertEqual(top["titulo"], "Validado")
        self.assertTrue(any("validado" in m for m in top["motivos"]))

    def test_mais_fresca_e_mais_barata_ganham(self):
        self.oferta("Antiga", 100, 1, criado="2026-11-16T10:00:00-03:00")
        self.oferta("Nova", 100, 2, criado="2026-11-22T08:00:00-03:00")
        self.assertEqual(piloto.candidatos(1, piloto.config(1), AGORA)[0]["titulo"], "Nova")

    def test_diversifica_categorias(self):
        self.oferta("Fone A", 50, 1, busca="fone")
        self.oferta("Fone B", 40, 2, busca="fone")
        self.oferta("Tênis A", 55, 3, busca="tenis")
        self.destino()
        self.ligar()
        r1 = self.ciclo()
        self.assertEqual(r1["titulo"], "Fone B")  # empate de categoria → mais barata
        db.conn().execute("UPDATE piloto_envios SET status='enviado'")
        db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
        db.conn().commit()
        r2 = self.ciclo(AGORA + timedelta(hours=1))
        self.assertTrue(r2["titulo"].startswith("Tênis"))  # não repete a categoria do último envio

    def test_nao_repete_a_mesma_oferta_para_o_cliente(self):
        self.oferta("Única", 50, 1)
        self.destino()
        self.ligar()
        self.assertEqual(self.ciclo()["acao"], "planejou")
        db.conn().execute("UPDATE piloto_envios SET status='enviado'")
        db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
        db.conn().commit()
        self.assertEqual(self.ciclo(AGORA + timedelta(hours=2))["acao"], "sem_estoque")


class TestPlanejarEDespachar(Base):
    def setUp(self):
        super().setUp()
        self.oferta("Fone Bluetooth", 99.9, 1)
        self.d1 = self.destino()
        self.d2 = self.destino("telegram", "@minhasofertas")

    def test_so_age_ligado_dentro_do_horario_e_com_destino(self):
        self.assertEqual(self.ciclo()["acao"], "desligado")
        self.ligar()
        self.assertEqual(self.ciclo(AGORA.replace(hour=3))["acao"], "fora_do_horario")
        self.assertEqual(self.ciclo(AGORA.replace(hour=21))["acao"], "fora_do_horario")
        db.conn().execute("DELETE FROM piloto_destinos")
        db.conn().commit()
        self.assertEqual(self.ciclo()["acao"], "sem_destinos")

    def test_uma_mensagem_por_destino_com_espacamento(self):
        self.ligar()
        r = self.ciclo()
        self.assertEqual((r["acao"], r["destinos"]), ("planejou", 2))
        rows = db.conn().execute("SELECT * FROM piloto_envios ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(_dt(rows[1]["enviar_apos"]) - _dt(rows[0]["enviar_apos"]), timedelta(seconds=10))
        self.assertIn("R$ 99,90", rows[0]["texto"])
        self.assertIn("https://meli.la/001", rows[0]["texto"])
        self.assertIn("encontrada", rows[0]["motivo"])

    def test_envia_um_por_chamada_e_respeita_o_horario_de_cada_um(self):
        self.ligar()
        self.ciclo()
        n_antes = len(self.g.whats)
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "enviado")  # o 1º sai na hora
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "nada")  # o 2º ainda não é a vez dele
        self.assertEqual(len(self.g.whats), n_antes + 1)
        self.assertEqual(piloto.despachar(1, AGORA + timedelta(seconds=11))["acao"], "enviado")
        self.assertEqual(self.g.telegram[-1][0], "@minhasofertas")
        self.assertEqual(self.g.telegram[-1][3], "https://meli.la/001")  # botão "Ver oferta"
        self.assertEqual(self.g.whats[-1][2], tp.JPG)  # foto do produto vai junto
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM piloto_envios WHERE status='enviado'").fetchone()[0], 2)

    def test_intervalo_e_limite_do_dia(self):
        self.ligar(intervalo_min=30, max_dia=1)
        self.ciclo()
        self.assertEqual(self.ciclo(AGORA + timedelta(minutes=5))["acao"], "aguardando_intervalo")
        self.assertEqual(self.ciclo(AGORA + timedelta(minutes=50))["acao"], "ciclo_em_andamento")
        db.conn().execute("UPDATE piloto_envios SET status='enviado'")
        db.conn().commit()
        self.assertEqual(self.ciclo(AGORA + timedelta(minutes=50))["acao"], "limite_do_dia")

    def test_falhas_seguidas_desligam_o_destino_e_avisam_o_dono(self):
        from thyfia import tenant
        tenant.salvar_canais(1, "34999990001", "")
        self.ligar(intervalo_min=10)
        self.g.falha_whats = ErroEnvio("recusou", "destino")
        t = AGORA
        for i in range(3):
            self.oferta(f"Oferta {i}", 50 + i, 10 + i)
            db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
            db.conn().commit()
            self.ciclo(t)
            piloto.despachar(1, t + timedelta(seconds=30))
            piloto._ultimo_wa[0] = 0.0
            db.conn().execute("UPDATE piloto_envios SET status='pulado' WHERE status='pendente'")
            db.conn().commit()
            t += timedelta(hours=1)
        d = db.conn().execute("SELECT ativo, falhas FROM piloto_destinos WHERE id=?", (self.d1["id"],)).fetchone()
        self.assertEqual((d["ativo"], d["falhas"]), (0, 3))
        self.assertTrue(any(j.startswith("privado:") and "desliguei" in x for j, x, _ in self.g.whats))

    def test_problema_de_conexao_pausa_sem_perder_a_oferta(self):
        self.ligar()
        self.ciclo()
        self.g.falha_whats = ErroEnvio("desconectado", "conexao")
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "pausado")
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM piloto_envios WHERE status='pendente'").fetchone()[0], 2)
        self.assertEqual(self.ciclo(AGORA + timedelta(minutes=5))["acao"], "pausado")
        self.g.falha_whats = None
        self.assertEqual(piloto.despachar(1, AGORA + timedelta(minutes=31))["acao"], "enviado")

    def test_limite_do_telegram_adia_o_envio(self):
        self.ligar()
        self.ciclo()
        piloto.despachar(1, AGORA)  # whatsapp
        self.g.falha_tg = ErroEnvio("espere", "limite", 40)
        self.assertEqual(piloto.despachar(1, AGORA + timedelta(seconds=11))["acao"], "adiado")
        self.g.falha_tg = None
        self.assertEqual(piloto.despachar(1, AGORA + timedelta(seconds=20))["acao"], "nada")
        self.assertEqual(piloto.despachar(1, AGORA + timedelta(seconds=60))["acao"], "enviado")

    def test_oferta_que_ficou_muito_tempo_na_fila_expira(self):
        self.ligar()
        self.ciclo()
        r = piloto.despachar(1, AGORA + timedelta(hours=4))
        self.assertEqual(r["acao"], "nada")
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM piloto_envios WHERE status='pulado'").fetchone()[0], 2)

    def test_destino_desligado_nao_recebe(self):
        self.ligar()
        self.ciclo()
        piloto.alternar_destino(1, self.d1["id"], False)
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "pulado")


class TestAprovacao(Base):
    def test_modo_aprovar_espera_o_dono(self):
        self.oferta("Fone", 80, 1)
        self.destino()
        self.ligar(modo="aprovar")
        r = self.ciclo()
        self.assertEqual(r["acao"], "aguardando_aprovacao")
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "nada")
        pend = piloto.pendentes_aprovacao(1)
        self.assertEqual(len(pend), 1)
        self.assertEqual(piloto.aprovar(1, pend[0]["item_id"], AGORA), 1)
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "enviado")

    def test_pular_nao_volta_a_ser_sugerida(self):
        self.oferta("Fone", 80, 1)
        self.destino()
        self.ligar(modo="aprovar")
        self.ciclo()
        piloto.pular(1, piloto.pendentes_aprovacao(1)[0]["item_id"])
        db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
        db.conn().commit()
        self.assertEqual(self.ciclo(AGORA + timedelta(hours=1))["acao"], "sem_estoque")


class TestRelatorioEEstoque(Base):
    def setUp(self):
        super().setUp()
        from thyfia import tenant
        tenant.salvar_canais(1, "34999990001", "")

    def test_avisa_uma_vez_por_dia_quando_acaba_o_estoque(self):
        self.destino()
        self.ligar()
        self.assertEqual(self.ciclo()["acao"], "sem_estoque")
        db.conn().execute("UPDATE piloto_config SET proximo_envio=NULL")
        db.conn().commit()
        self.ciclo(AGORA + timedelta(hours=1))
        avisos = [x for j, x, _ in self.g.whats if j.startswith("privado:") and "acabaram" in x]
        self.assertEqual(len(avisos), 1)

    def test_relatorio_diario_depois_das_20h_uma_vez(self):
        self.oferta("Fone", 80, 1)
        self.destino()
        self.ligar()
        self.ciclo()
        piloto.despachar(1, AGORA)
        self.assertEqual(piloto.enviar_relatorios(AGORA.replace(hour=19)), 0)
        self.assertEqual(piloto.enviar_relatorios(AGORA.replace(hour=20, minute=5)), 1)
        self.assertEqual(piloto.enviar_relatorios(AGORA.replace(hour=21)), 0)
        rel = [x for j, x, _ in self.g.whats if j.startswith("privado:") and "resumo de hoje" in x][0]
        self.assertIn("1 oferta(s) divulgada(s)", rel)
        self.assertIn("Estoque baixo", rel)


class TestMensagens(unittest.TestCase):
    ITEM = {"id": 7, "titulo": "FONE DE OUVIDO BLUETOOTH <script>", "preco": 1234.5, "busca": "fone",
            "link_afiliado": "https://meli.la/abc"}

    def test_nao_inventa_nada_e_traz_link_e_preco(self):
        m = mensagens.compor(self.ITEM)
        self.assertIn("R$ 1.234,50", m["texto"])
        self.assertIn("https://meli.la/abc", m["texto"])
        self.assertNotIn("%", m["texto"])  # nenhum desconto inventado
        self.assertIn(mensagens.AVISO, m["texto"])
        self.assertEqual(mensagens.emoji_da("fone", "Fone Bluetooth"), "🎧")

    def test_html_do_telegram_escapa(self):
        self.assertNotIn("<script>", mensagens.compor(self.ITEM)["html"])

    def test_titulo_gritado_e_longo_fica_legivel(self):
        t = mensagens.limpar_titulo("PROMOÇÃO " + "ABC " * 60)
        self.assertLessEqual(len(t), 111)
        self.assertFalse(t.isupper())

    def test_varia_entre_destinos(self):
        textos = {mensagens.compor(self.ITEM, "variado", d)["texto"] for d in range(12)}
        self.assertGreater(len(textos), 3)

    def test_mesma_entrada_mesma_saida(self):
        self.assertEqual(mensagens.compor(self.ITEM, "animado", 3), mensagens.compor(self.ITEM, "animado", 3))


class TestPlanoEEnvio(Base):
    def test_piloto_exige_plano_pro(self):
        planos.conceder("t@t", "plus")
        u = db.conn().execute("SELECT * FROM usuarios WHERE id=1").fetchone()
        with self.assertRaises(HTTPException) as cm:
            app_mod.exige("automacao")(u=u)
        self.assertEqual(cm.exception.status_code, 402)
        planos.conceder("t@t", "pro")
        self.assertIsNotNone(app_mod.exige("automacao")(u=u))

    def test_status_traz_painel_completo_sem_segredos(self):
        self.oferta("Fone", 80, 1)
        self.destino()
        s = piloto.status(1)
        self.assertEqual(s["estoque"]["total"], 1)
        self.assertEqual(len(s["destinos"]), 1)
        self.assertNotIn("codigo", s["destinos"][0])
        self.assertIn("plataforma", s)

    def test_erros_de_envio_sao_classificados(self):
        with mock.patch.object(envio_canais, "_evolution", return_value=("https://e.test", "k", "i")), \
                mock.patch.object(envio_canais.requests, "request") as rq:
            for status, tipo in ((401, "config"), (503, "conexao"), (400, "destino")):
                rq.return_value = mock.Mock(status_code=status, json=lambda: {"message": "x"})
                with self.assertRaises(ErroEnvio) as cm:
                    envio_canais._chamar("POST", "https://e.test/x", "k")
                self.assertEqual(cm.exception.tipo, tipo)
            rq.return_value = mock.Mock(status_code=400, json=lambda: {"message": "Connection Closed"})
            with self.assertRaises(ErroEnvio) as cm:
                envio_canais._chamar("POST", "https://e.test/x", "k")
            self.assertEqual(cm.exception.tipo, "conexao")


def _dt(s):
    return datetime.fromisoformat(s)


if __name__ == "__main__":
    unittest.main()


G2 = "120363000000000003@g.us"
G3 = "120363000000000004@g.us"


class EvolutionFalsa:
    """O WhatsApp do cliente: estado da conexão, QR e a lista de grupos dele."""

    def __init__(self):
        self.estado = "inexistente"
        self.numero = "5534999990000"
        self.grupos = [{"jid": GRUPO, "nome": "Achadinhos VIP", "tamanho": 120},
                       {"jid": G2, "nome": "Família", "tamanho": 12},
                       {"jid": G3, "nome": "Ofertas Tech", "tamanho": 300}]
        self.chamadas: list[str] = []

    def wa_estado(self, inst):
        self.chamadas.append(f"estado:{inst}")
        return self.estado

    def wa_conectar(self, inst):
        self.chamadas.append(f"conectar:{inst}")
        if self.estado == "conectado":
            return {"estado": "conectado", "qr": ""}
        self.estado = "conectando"
        return {"estado": "conectando", "qr": "data:image/png;base64,QR"}

    def wa_numero(self, inst):
        return self.numero

    def wa_grupos(self, inst):
        return list(self.grupos)

    def wa_desconectar(self, inst):
        self.estado = "desconectado"

    def aplicar(self, teste):
        for nome in ("wa_estado", "wa_conectar", "wa_numero", "wa_grupos", "wa_desconectar"):
            p = mock.patch.object(envio_canais, nome, getattr(self, nome))
            p.start()
            teste.addCleanup(p.stop)
        return self


class TestWhatsappDoCliente(Base):
    def setUp(self):
        super().setUp()
        self.ev = EvolutionFalsa().aplicar(self)

    def conectar(self):
        piloto.whatsapp_conectar(1)
        self.ev.estado = "conectado"  # o cliente leu o QR

    def test_qr_e_conexao(self):
        self.assertEqual(piloto.whatsapp_status(1)["estado"], "nunca")
        r = piloto.whatsapp_conectar(1)
        self.assertEqual((r["estado"], r["qr"]), ("conectando", "data:image/png;base64,QR"))
        self.assertEqual(piloto.whatsapp_status(1)["estado"], "conectando")
        self.assertIn("conectar:thyfia-u1", self.ev.chamadas)
        self.ev.estado = "conectado"
        st = piloto.whatsapp_status(1, atualizar=True)
        self.assertEqual((st["estado"], st["numero"]), ("conectado", "5534999990000"))
        self.assertEqual(piloto.whatsapp_conectar(1)["qr"], "")  # já conectado: não pede QR de novo

    def test_numero_do_dono_nao_e_sobrescrito(self):
        tenant_linha = db.conn()
        tenant_linha.execute("INSERT OR IGNORE INTO config_usuario (usuario_id) VALUES (1)")
        tenant_linha.execute("UPDATE config_usuario SET whatsapp_numero='5511888887777' WHERE usuario_id=1")
        tenant_linha.commit()
        self.conectar()
        self.assertEqual(piloto.whatsapp_status(1, atualizar=True)["numero"], "5511888887777")

    def test_grupos_exigem_conexao(self):
        for f in (lambda: piloto.grupos_whatsapp(1), lambda: piloto.salvar_grupos(1, [GRUPO])):
            with self.assertRaises(piloto.ErroPiloto) as e:
                f()
            self.assertEqual(e.exception.status, 409)

    def test_lista_e_marca_selecionados(self):
        self.conectar()
        piloto.salvar_grupos(1, [GRUPO])
        lista = {g["jid"]: g["selecionado"] for g in piloto.grupos_whatsapp(1)}
        self.assertEqual(lista, {GRUPO: True, G2: False, G3: False})

    def test_salvar_define_exatamente_a_selecao(self):
        self.conectar()
        destinos = piloto.salvar_grupos(1, [GRUPO, G3])
        self.assertEqual({d["ref"] for d in destinos}, {GRUPO, G3})
        self.assertTrue(all(d["verificado"] and d["proprio"] for d in destinos))  # sem código de confirmação
        destinos = piloto.salvar_grupos(1, [G2, G3])  # troca: sai um, entra outro, outro permanece
        self.assertEqual({d["ref"] for d in destinos}, {G2, G3})
        self.assertEqual(piloto.salvar_grupos(1, []), [])

    def test_nao_remove_destino_de_outro_tipo_nem_aceita_grupo_alheio(self):
        self.destino("telegram", "@minhasofertas")
        self.conectar()
        destinos = piloto.salvar_grupos(1, [GRUPO])
        self.assertEqual({d["tipo"] for d in destinos}, {"telegram", "whatsapp_grupo"})
        with self.assertRaises(piloto.ErroPiloto):
            piloto.salvar_grupos(1, ["999@g.us"])  # não está no WhatsApp dele

    def test_respeita_limite_total_de_destinos(self):
        self.conectar()
        self.ev.grupos = [{"jid": f"{i}@g.us", "nome": f"G{i}", "tamanho": 1} for i in range(piloto.MAX_DESTINOS + 1)]
        with self.assertRaises(piloto.ErroPiloto):
            piloto.salvar_grupos(1, [g["jid"] for g in self.ev.grupos])

    def test_envia_pela_instancia_do_cliente(self):
        self.conectar()
        piloto.salvar_grupos(1, [GRUPO])
        self.oferta("Fone Bluetooth", 99.9, 1)
        self.ligar()
        self.ciclo()
        self.assertEqual(piloto.despachar(1, AGORA)["acao"], "enviado")
        self.assertEqual(self.g.instancias[-1], "thyfia-u1")
        self.assertEqual(self.g.whats[-1][0], GRUPO)

    def test_destino_pela_plataforma_continua_usando_o_numero_da_thyfia(self):
        self.destino()  # modelo antigo: código no grupo
        self.oferta("Fone Bluetooth", 99.9, 1)
        self.ligar()
        self.ciclo()
        piloto.despachar(1, AGORA)
        self.assertIsNone(self.g.instancias[-1])

    def test_whatsapp_caiu_pausa_e_avisa_o_dono(self):
        self.conectar()
        piloto.salvar_grupos(1, [GRUPO])
        db.conn().execute("UPDATE config_usuario SET whatsapp_numero='5534999990000' WHERE usuario_id=1")
        db.conn().commit()
        self.oferta("Fone Bluetooth", 99.9, 1)
        self.ligar()
        self.ciclo()
        self.g.falha_whats = ErroEnvio("connection closed", "conexao")
        r = piloto.despachar(1, AGORA)
        self.assertEqual(r["acao"], "pausado")
        self.assertEqual(piloto.whatsapp_status(1)["estado"], "desconectado")
        self.assertTrue(any("QR" in t for t in self.g.ordem + [w[1] for w in self.g.whats]))
        self.assertEqual(db.conn().execute("SELECT status FROM piloto_envios").fetchone()[0], "pendente")

    def test_desconectar(self):
        self.conectar()
        piloto.whatsapp_conectar(1)
        self.assertEqual(piloto.whatsapp_desconectar(1)["estado"], "desconectado")

    def test_avisos_ao_dono_saem_pelo_whatsapp_dele_quando_conectado(self):
        self.conectar()
        piloto.whatsapp_status(1, atualizar=True)  # guarda número e estado
        piloto._avisar(1, "oi")
        self.assertEqual(self.g.instancias[-1], "thyfia-u1")

    def test_canal_pelo_proprio_numero_dispensa_codigo(self):
        self.conectar()
        d = piloto.adicionar_destino(1, "whatsapp_canal", CANAL, "Meu canal", meu_numero=True)
        self.assertTrue(d["verificado"] and d["proprio"])


class TestEvolutionRespostas(unittest.TestCase):
    """Os leitores aceitam as formas de resposta da Evolution v2 (QR em lugares diferentes)."""

    def resp(self, dados, status=200):
        r = mock.Mock(status_code=status)
        r.json.return_value = dados
        return r

    def setUp(self):
        for k, v in (("evolution_url", "http://evo"), ("evolution_apikey", "k"), ("evolution_instancia", "plataforma")):
            p = mock.patch.object(configuracoes, "obter", side_effect=lambda c, _m={"evolution_url": "http://evo",
                                  "evolution_apikey": "k", "evolution_instancia": "plataforma"}: _m.get(c, ""))
            p.start()
            self.addCleanup(p.stop)
            break

    def test_estado(self):
        for dados, esperado in (({"instance": {"state": "open"}}, "conectado"),
                                ({"state": "connecting"}, "conectando"),
                                ({"instance": {"state": "close"}}, "desconectado")):
            with mock.patch.object(envio_canais, "_chamar", return_value=self.resp(dados)):
                self.assertEqual(envio_canais.wa_estado("x"), esperado)
        with mock.patch.object(envio_canais, "_chamar", return_value=self.resp({}, 404)):
            self.assertEqual(envio_canais.wa_estado("x"), "inexistente")

    def test_qr_na_criacao_e_no_connect(self):
        chamadas = [self.resp({}, 404), self.resp({"qrcode": {"base64": "AAA"}, "instance": {}})]
        with mock.patch.object(envio_canais, "_chamar", side_effect=chamadas):
            r = envio_canais.wa_conectar("thyfia-u9")
        self.assertEqual(r, {"estado": "conectando", "qr": "data:image/png;base64,AAA"})
        chamadas = [self.resp({"instance": {"state": "close"}}), self.resp({"base64": "data:image/png;base64,BBB"})]
        with mock.patch.object(envio_canais, "_chamar", side_effect=chamadas):
            self.assertEqual(envio_canais.wa_conectar("thyfia-u9")["qr"], "data:image/png;base64,BBB")

    def test_numero_e_grupos(self):
        with mock.patch.object(envio_canais, "_chamar", return_value=self.resp(
                [{"instance": {"owner": "5534999990000:12@s.whatsapp.net"}}])):
            self.assertEqual(envio_canais.wa_numero("x"), "5534999990000")
        with mock.patch.object(envio_canais, "_chamar", return_value=self.resp(
                [{"id": "2@g.us", "subject": "beta", "size": 5}, {"id": "1@g.us", "subject": "Alfa"},
                 {"id": "x@s.whatsapp.net", "subject": "não é grupo"}])):
            g = envio_canais.wa_grupos("x")
        self.assertEqual([x["nome"] for x in g], ["Alfa", "beta"])
