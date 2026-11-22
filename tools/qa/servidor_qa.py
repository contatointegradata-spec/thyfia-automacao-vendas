"""Servidor local com dados de teste para o QA de layout (não usar em produção).

Sobe a ThyfIA com um usuário de teste (teste@t.com / senha12345), a Evolution API falsa (QR e grupos),
ofertas com títulos enormes, quedas de preço e dados de aprendizado — o suficiente para exercitar todas
as telas. Variáveis: QA_PORTA (8767), QA_PLANO (premium), QA_ADMIN (1).
"""
import os, sys, tempfile
from pathlib import Path
PORTA = int(os.environ.get("QA_PORTA", "8767"))
PLANO = os.environ.get("QA_PLANO", "premium")      # plus | pro | premium
ADMIN = os.environ.get("QA_ADMIN", "1") == "1"
DADOS = os.path.join(tempfile.gettempdir(), "thyfia_qa_dados")
os.environ["THYFIA_DADOS"] = DADOS
os.environ["THYFIA_PILOTO"] = "0"; os.environ["THYFIA_AGENDADOR"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import shutil; shutil.rmtree(DADOS, ignore_errors=True)
from unittest import mock
from thyfia import app as A, db, auth, planos, envio_canais, configuracoes, itens, imagem_host
db.init()
import time, io, base64
from PIL import Image
_t = {"ini": None, "conectado": False}
def _png():
    im = Image.new("RGB", (60, 60), "white")
    for x in range(60):
        for y in range(60):
            if (x // 6 + y // 6 + int(time.time() // 5)) % 3 == 0: im.putpixel((x, y), (0, 0, 0))
    b = io.BytesIO(); im.save(b, "PNG"); return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
def wa_estado(i):
    if _t["conectado"]: return "conectado"
    if _t["ini"] and time.time() - _t["ini"] > 9: _t["conectado"] = True; return "conectado"
    return "conectando" if _t["ini"] else "inexistente"
def wa_conectar(i):
    if _t["ini"] is None: _t["ini"] = time.time()
    st = wa_estado(i)
    return {"estado": st, "qr": "" if st == "conectado" else _png()}
GR = [{"jid": f"1203630000000000{n:02d}@g.us", "nome": nm, "tamanho": tm} for n, (nm, tm) in enumerate([("Achadinhos VIP", 220), ("Família Silva", 14), ("Ofertas Tech BR", 480), ("Promoções do Dia", 1020), ("Churrasco sábado", 9)], 1)]
for nome, fn in (("wa_estado", wa_estado), ("wa_conectar", wa_conectar), ("wa_numero", lambda i: "5534988887777"),
                 ("wa_grupos", lambda i: GR), ("wa_desconectar", lambda i: _t.update(conectado=False, ini=None))):
    setattr(envio_canais, nome, fn)
for nome, fn in (("whatsapp_enviar", lambda j, t, i=None, inst=None: print("WA ->", j, t[:40].replace("\n"," "), flush=True)),
                 ("telegram_enviar", lambda c, h, i, l: print("TG ->", c, flush=True)),
                 ("telegram_texto", lambda c, t: print("TG txt ->", c, t[:60], flush=True)),
                 ("whatsapp_texto_privado", lambda n, t, i=None: print("PRIV ->", n, flush=True)),
                 ("whatsapp_resolver_convite", lambda e: {"jid": "120363111111111111@g.us", "nome": "Achadinhos VIP"})):
    setattr(envio_canais, nome, fn)
imagem_host.obter = lambda n=None: type("H", (), {"obter_url": lambda s, it: it.get("imagem")})()
configuracoes.definir("evolution_url", "https://e.test"); configuracoes.definir("evolution_apikey", "k")
configuracoes.definir("evolution_instancia", "i"); configuracoes.definir("evolution_numero", "5534999990000")
configuracoes.definir("telegram_bot_token", "t"); configuracoes.definir("telegram_bot", "@thyfia_bot")
c = db.conn()
uid = auth.criar_usuario("Teste Pro", "teste@t.com", "senha12345")
planos.conceder("teste@t.com", PLANO)
if ADMIN:
    db.conn().execute("UPDATE usuarios SET papel='admin' WHERE email=?", ("teste@t.com",)); db.conn().commit()
from thyfia import tenant
tenant.salvar_canais(uid, "34999990001", "")
c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, status) VALUES ('j1',?,?,20,'concluido')", (uid, "fone bluetooth"))
c.commit()
from datetime import datetime
from thyfia.itens import TZ_SP
agora = datetime.now(TZ_SP).isoformat(timespec="seconds")
for i, (t, p, b) in enumerate([("Fone de Ouvido Bluetooth TWS Pro", 89.9, "fone bluetooth"), ("Fone Gamer Headset RGB", 129.0, "fone bluetooth"),
        ("Tênis Corrida Masculino Leve", 159.9, "tenis corrida"), ("Air Fryer Digital 4L", 249.0, "air fryer"), ("Carregador Turbo 65W", 59.9, "carregador"), ("Capacete Moto Fechado Tam 58", 189.0, "capacete moto"), ("Whey Protein 900g", 119.0, "whey protein")]):
    itens.salvar_item("j1", b, t, p, f"https://produto.mercadolivre.com.br/MLB-{i:04d}", f"https://meli.la/pl{i}", criado_em=agora, imagem="https://http2.mlstatic.com/D_NQ_NP_2X_608923-MLB120463575227_102026-F.jpg")
from thyfia import aprendizado, imagem_host as _ih
from datetime import timedelta
_ih.url_base_publica = lambda: "https://thyfia.test"
aprendizado.auto_treinar("creatina monohidratada", [{"titulo": "Creatina Monohidratada Pura"}] * 4)
aprendizado.auto_treinar("whey isolado hidrolisado", [{"titulo": "Whey Isolado Hidrolisado 900g"}] * 4)
for i in range(12):
    aprendizado.registrar_resultado(uid, "fit", "whey protein", 119.0, "B", i < 9)
    aprendizado.registrar_resultado(uid, "tech", "fone bluetooth", 89.0, "A", i < 2)
    aprendizado.registrar_resultado(uid, "casa", "air fryer", 249.0, "C", i < 5)
for d, p in ((5, 119.0), (4, 119.0), (0, 89.9)):
    aprendizado.registrar_precos([{"url": "https://produto.mercadolivre.com.br/MLB-0000", "preco": p}], datetime.now() - timedelta(days=d))

# ---- dados de estresse: títulos/termos enormes, como o Mercado Livre devolve ----
LONGOS = [
 ("Kit 3 Fones De Ouvido Bluetooth Sem Fio Tws Pro Max Ultra Premium Com Cancelamento De Ruído Ativo Bateria 40h Resistente À Água IPX7 Compatível Com Android E iOS Original Lacrado", 1299.99, "fone bluetooth"),
 ("SuperlongaPalavraSemEspacoQueNaoQuebraNuncaEEstouraOContainerDoCardEmQualquerLarguraDeTela1234567890", 99.0, "palavra enorme sem espaco"),
 ("Whey Protein Concentrado 900g Sabor Chocolate Belga Com Avelã E Caramelo Salgado Edição Limitada Importado", 4999.0, "whey protein"),
]
for i, (t, p_, b) in enumerate(LONGOS):
    itens.salvar_item("j1", b, t, p_, f"https://produto.mercadolivre.com.br/MLB-9{i:03d}", f"https://meli.la/long{i}", criado_em=agora, imagem="https://http2.mlstatic.com/D_NQ_NP_2X_608923-MLB120463575227_102026-F.jpg")
c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, status) VALUES ('j2',?,?,50,'concluido')", (uid, "termo de busca muito longo que alguém digitou por engano sem pensar no layout da tela de histórico e que precisa quebrar"))
c.execute("INSERT INTO jobs (id, usuario_id, termo, limite, status) VALUES ('j3',?,?,10,'erro')", (uid, "kit ferramentas"))
c.commit()

for t,b,ok,tot in [("whey",4,5,12),("creatina",1,10,10),("super coffee",1,8,8),("pre treino",1,6,6),("fone bluetooth jbl com cancelamento de ruído ativo e microfone embutido original lacrado",2,3,9)]:
    c.execute("INSERT OR REPLACE INTO termos (termo,buscas,links_ok,links_total,ultima) VALUES (?,?,?,?,?)",(t,b,ok,tot,agora))
c.commit()
import uvicorn
uvicorn.run(A.app, host="127.0.0.1", port=PORTA, log_level="warning")
