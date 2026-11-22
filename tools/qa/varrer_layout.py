"""QA de layout da ThyfIA: percorre todas as telas e estados em vários tamanhos de janela e acusa
vazamento, texto cortado, texto coberto por outro elemento e sobreposição; salva capturas de tela.

Uso (com tools/qa/servidor_qa.py no ar):  python tools/qa/varrer_layout.py [saida.json] [larguras...]
Variáveis: QA_URL (http://127.0.0.1:8767), QA_CHROME (caminho do Chromium; vazio = o do Playwright), QA_CAPTURAS.
Requer:  pip install playwright && playwright install chromium
"""
import json, sys, os
from playwright.sync_api import sync_playwright

CH = os.environ.get("QA_CHROME") or None
B = os.environ.get("QA_URL", "http://127.0.0.1:8767")
OUT = sys.argv[1] if len(sys.argv) > 1 else "qa_resultado.json"
SHOTS = os.environ.get("QA_CAPTURAS", "qa_capturas")
os.makedirs(SHOTS, exist_ok=True)
VIEWPORTS = [(1210, 690), (1440, 900), (1024, 768), (768, 1024), (390, 844)]
if len(sys.argv) > 2:
    VIEWPORTS = [v for v in VIEWPORTS if str(v[0]) in sys.argv[2:]]

VIEWS = ["gerar", "integracoes", "automacao", "piloto", "publicacao", "historico", "cerebro", "plano", "ajuda", "admin"]

DETECTOR = r"""
(raiz) => {
  const R = document.querySelector(raiz) || document.body;
  const vis = (el) => {
    const d = el.closest('details');
    if (d && !d.open && !el.closest('summary')) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const desc = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const c = (el.className && el.className.baseVal === undefined ? String(el.className) : '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
    if (c.length) s += '.' + c.join('.');
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50);
    return s + (t ? ` "${t}"` : '');
  };
  const out = [];
  const vw = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > vw + 1)
    out.push({tipo: 'scroll-horizontal', det: `página ${document.documentElement.scrollWidth}px > janela ${vw}px`});

  const todos = [...R.querySelectorAll('*')].filter(vis);
  const temTexto = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());

  for (const el of todos) {
    if (['SCRIPT','STYLE','svg','path','CANVAS'].includes(el.tagName)) continue;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    // 1) conteúdo escondido/cortado pelo próprio elemento
    const ox = cs.overflowX, oy = cs.overflowY;
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0 && ['hidden','clip'].includes(ox) && cs.textOverflow !== 'ellipsis')
      out.push({tipo: 'cortado-horizontal', det: desc(el), extra: `${el.scrollWidth}>${el.clientWidth}`});
    if (el.scrollHeight > el.clientHeight + 2 && el.clientHeight > 0 && ['hidden','clip'].includes(oy) && temTexto(el))
      out.push({tipo: 'cortado-vertical', det: desc(el), extra: `${el.scrollHeight}>${el.clientHeight}`});
    if (cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1)
      out.push({tipo: 'reticencias', det: desc(el), extra: `${el.scrollWidth}>${el.clientWidth}`});
    // 2) texto vazando para fora do pai (pai com overflow visível)
    if (temTexto(el) && el.parentElement && el.parentElement !== document.body) {
      const p = el.parentElement, pr = p.getBoundingClientRect(), pcs = getComputedStyle(p);
      if (pcs.overflowX === 'visible' && !['absolute','fixed'].includes(cs.position)) {
        // medir o texto de verdade (range), não a caixa do bloco
        const rg = document.createRange(); rg.selectNodeContents(el);
        const tr = rg.getBoundingClientRect();
        if (tr.width && (tr.right > pr.right + 1.5 || tr.left < pr.left - 1.5) && pr.width > 40)
          out.push({tipo: 'texto-vaza-do-pai', det: desc(el), extra: `texto ${Math.round(tr.left)}–${Math.round(tr.right)} | pai ${Math.round(pr.left)}–${Math.round(pr.right)} (${desc(p).slice(0,40)})`});
      }
    }
    // 3) fora da janela horizontalmente
    if (r.right > vw + 2 && cs.position !== 'fixed' && temTexto(el))
      out.push({tipo: 'fora-da-janela', det: desc(el), extra: `direita=${Math.round(r.right)} janela=${vw}`});
  }

  // 4) sobreposição de textos (folhas com texto que não são ancestrais uma da outra)
  const folhas = todos.filter(el => temTexto(el) && !['OPTION','SCRIPT','STYLE'].includes(el.tagName));
  const rects = folhas.map(el => {
    const rg = document.createRange(); rg.selectNodeContents(el);
    const rs = [...rg.getClientRects()].filter(x => x.width > 2 && x.height > 2);
    return rs;
  });
  const hit = (a, b) => {
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return w > 3 && h > 3 ? w * h : 0;
  };
  const vistos = new Set();
  for (let i = 0; i < folhas.length; i++) for (let j = i + 1; j < folhas.length; j++) {
    const a = folhas[i], b = folhas[j];
    if (a.contains(b) || b.contains(a)) continue;
    let area = 0;
    for (const ra of rects[i]) for (const rb of rects[j]) area = Math.max(area, hit(ra, rb));
    if (area > 12) {
      const k = i + '-' + j;
      if (!vistos.has(k)) { vistos.add(k); out.push({tipo: 'sobreposicao', det: desc(a), extra: 'x ' + desc(b) + ` (${Math.round(area)}px²)`}); }
    }
  }
  // 5) texto coberto por outro elemento (z-index/sobreposição), medido no ponto central
  const cob = new Set();
  for (const el of folhas) {
    if (el.closest('.bloqueio, [hidden], .menu-ind')) continue;
    const rg = document.createRange(); rg.selectNodeContents(el);
    const q = [...rg.getClientRects()].find(x => x.width > 4 && x.height > 4);
    if (!q) continue;
    const sc = el.closest('.conteudo, .overlay, .modal');
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const q2 = [...rg.getClientRects()].find(x => x.width > 4 && x.height > 4);
    if (!q2 || q2.top < 0 || q2.bottom > innerHeight) continue;
    const x = q2.left + Math.min(q2.width / 2, 40), y = q2.top + q2.height / 2;
    const topo = document.elementFromPoint(x, y);
    if (!topo || topo === el || el.contains(topo) || topo.contains(el)) continue;
    if (getComputedStyle(topo).pointerEvents === 'none') continue;
    const k = desc(topo);
    if (cob.has(k + desc(el))) continue; cob.add(k + desc(el));
    out.push({tipo: 'coberto-por', det: desc(el), extra: 'coberto por ' + desc(topo)});
  }
  const sc0 = document.querySelector('.conteudo'); if (sc0) sc0.scrollTo(0, 0);
  return out;
}
"""

SIDEBAR = r"""
() => {
  const s = document.querySelector('#tela-app aside, .sidebar, #sidebar') || document.querySelector('[class*=side]');
  if (!s) return [];
  const r = s.getBoundingClientRect(), out = [];
  const cs = getComputedStyle(s);
  const filhos = [...s.querySelectorAll('*')].filter(e => { const q = e.getBoundingClientRect(); return q.width && q.height; });
  let maxB = 0; let quem = null;
  for (const f of filhos) { const b = f.getBoundingClientRect().bottom; if (b > maxB) { maxB = b; quem = f; } }
  const vh = window.innerHeight;
  const rolavel = ['auto','scroll'].includes(cs.overflowY);
  if (maxB > Math.min(r.bottom, vh) + 2 && !rolavel)
    out.push({tipo: 'sidebar-cortada', det: s.tagName + '.' + s.className, extra: `conteúdo termina em ${Math.round(maxB)} | janela ${vh} | caixa ${Math.round(r.bottom)} | overflow-y=${cs.overflowY}`});
  return out;
}
"""


def varrer(pg, rotulo, vp, raiz, achados, shots=True):
    for a in pg.evaluate(DETECTOR, raiz):
        a.update(tela=rotulo, vp=f"{vp[0]}x{vp[1]}")
        achados.append(a)
    for a in pg.evaluate(SIDEBAR):
        a.update(tela=rotulo, vp=f"{vp[0]}x{vp[1]}")
        achados.append(a)
    if shots:
        h = pg.evaluate("(() => { const c = document.querySelector('.conteudo'); return c && c.offsetParent !== null ? [c.scrollHeight, c.clientHeight] : [0, 0]; })()")
        if h[0] and h[0] > h[1] + 4:
            n = 0; y = 0
            while y < h[0] and n < 8:
                pg.evaluate(f"document.querySelector('.conteudo').scrollTo(0, {y})"); pg.wait_for_timeout(120)
                pg.screenshot(path=f"{SHOTS}/{vp[0]}_{rotulo}_s{n}.png")
                n += 1; y += max(h[1] - 60, 200)
            pg.evaluate("document.querySelector('.conteudo').scrollTo(0, 0)")
        else:
            pg.screenshot(path=f"{SHOTS}/{vp[0]}_{rotulo}_s0.png")


def main():
    achados = []
    erros_js = []
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=CH, args=["--no-proxy-server"]) if CH else p.chromium.launch(args=["--no-proxy-server"])
        for vp in VIEWPORTS:
            ctx = br.new_context(viewport={"width": vp[0], "height": vp[1]}, device_scale_factor=1)
            pg = ctx.new_page(); pg.set_default_timeout(4000)
            pg.on("pageerror", lambda e: erros_js.append(str(e)))
            # tela de login/cadastro (deslogado)
            pg.goto(B + "/"); pg.wait_for_timeout(900)
            varrer(pg, "auth", vp, "#tela-auth", achados)
            for aba in pg.query_selector_all("#tela-auth [data-aba], #tela-auth .aba, #tela-auth [role=tab]")[1:2]:
                try:
                    aba.click(); pg.wait_for_timeout(300)
                    varrer(pg, "auth-cadastro", vp, "#tela-auth", achados)
                except Exception:
                    pass
            pg.request.post(B + "/api/login", data=json.dumps({"email": "teste@t.com", "senha": "senha12345"}),
                            headers={"Content-Type": "application/json"})
            pg.goto(B + "/"); pg.wait_for_timeout(1500)
            for v in VIEWS:
                btn = pg.query_selector(f'[data-view="{v}"]')
                if not btn:
                    continue
                if pg.is_visible('[data-view="%s"]' % v):
                    btn.click()
                elif vp[0] <= 900:
                    # menu recolhido no mobile
                    t = pg.query_selector("#btn-menu, .menu-toggle, .hamb, [aria-label*=enu]")
                    if t: t.click(); pg.wait_for_timeout(200)
                    btn.click()
                pg.wait_for_timeout(1500)
                varrer(pg, v, vp, f"#view-{v}", achados)
            # estados especiais
            pg.click('[data-view="gerar"]'); pg.wait_for_timeout(500)
            try:
                pg.evaluate("document.querySelectorAll('.bloqueio').forEach(b => b.hidden = true)")
                pg.evaluate("document.querySelector('#view-gerar input[type=text], #view-gerar input:not([type])').focus()"); pg.wait_for_timeout(700)
                varrer(pg, "gerar-sugestoes", vp, "#view-gerar", achados)
                pg.evaluate("document.activeElement && document.activeElement.blur()")
            except Exception as e:
                print("sugestoes:", e)
            # overlays e modais
            for ov in ["overlay-gerando", "modal-ml", "overlay-logout"]:
                try:
                    pg.evaluate(f"document.getElementById('{ov}').hidden = false"); pg.wait_for_timeout(400)
                    varrer(pg, ov, vp, "#" + ov, achados)
                    pg.evaluate(f"document.getElementById('{ov}').hidden = true")
                except Exception as e:
                    print(ov, e)
            # toasts com mensagens longas
            try:
                pg.evaluate("""() => { toast('Não foi possível concluir porque o Mercado Livre devolveu um erro inesperado ao validar a sua conta de afiliado, tente novamente em alguns minutos.', 'erro'); toast('Ok!', 'ok'); toast('Palavra'+'enormeSemEspaco'.repeat(12), 'info'); }""")
                pg.wait_for_timeout(400); varrer(pg, "toasts", vp, "#toasts", achados)
            except Exception as e:
                print("toasts", e)
            # piloto: QR e grupos
            try:
                pg.click('[data-view="piloto"]'); pg.wait_for_timeout(1200)
                pg.click("#btn-wa-conectar"); pg.wait_for_selector(".pl-qr img", timeout=5000)
                varrer(pg, "piloto-qr", vp, "#view-piloto", achados)
                pg.wait_for_selector("#pl-g-lista .pl-grupo", timeout=20000); pg.wait_for_timeout(400)
                pg.locator(".pl-grupo").nth(0).click(); pg.locator(".pl-grupo").nth(2).click()
                varrer(pg, "piloto-grupos", vp, "#view-piloto", achados)
                pg.click("#btn-g-salvar"); pg.wait_for_timeout(1200)
                varrer(pg, "piloto-conectado", vp, "#view-piloto", achados)
            except Exception as e:
                print("piloto-qr", str(e)[:200])
            for ab in ["usuarios", "pagamentos", "config"]:
                try:
                    pg.click('[data-view="admin"]'); pg.wait_for_timeout(500)
                    pg.click(f'.ad-aba[data-aba="{ab}"]'); pg.wait_for_timeout(1200)
                    varrer(pg, f"admin-{ab}", vp, "#view-admin", achados)
                except Exception as e:
                    print("admin", ab, e)
            ctx.close()
        br.close()
    json.dump({"achados": achados, "erros_js": erros_js}, open(OUT, "w"), ensure_ascii=False, indent=1)
    # resumo
    from collections import Counter
    c = Counter((a["tela"], a["tipo"]) for a in achados)
    print(f"{len(achados)} achados | erros js: {len(erros_js)}")
    for (t, k), n in sorted(c.items()):
        print(f"  {t:18} {k:22} {n}")


main()
