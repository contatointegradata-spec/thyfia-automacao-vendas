// ThyfIA Conector — abre o login do Mercado Livre numa janelinha deste navegador,
// detecta quando a pessoa está logada e entrega a sessão para a ThyfIA.
const HUB = "https://www.mercadolivre.com.br/afiliados/hub?is_affiliate=true";
const DOMINIOS = ["mercadolivre.com.br", "mercadolivre.com", "mercadolibre.com"];
const TEMPO_LIMITE = 5 * 60 * 1000;

const logadoUrl = (url = "") =>
  url.includes("/afiliados") && !/login|registration|jms\//.test(url);

const MAPA_SAMESITE = { no_restriction: "None", lax: "Lax", strict: "Strict", unspecified: "Lax" };

async function coletarCookies() {
  const vistos = new Map();
  for (const domain of DOMINIOS) {
    for (const c of await chrome.cookies.getAll({ domain })) {
      vistos.set(`${c.name}|${c.domain}|${c.path}`, {
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        expires: c.session ? -1 : Math.floor(c.expirationDate || -1),
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: MAPA_SAMESITE[c.sameSite] || "Lax",
      });
    }
  }
  return [...vistos.values()];
}

// Já está logado neste navegador? Então não precisa abrir janelinha: o próprio navegador responde.
async function jaLogado() {
  try {
    const r = await fetch(HUB, { credentials: "include" });
    return r.ok && logadoUrl(r.url);
  } catch { return false; }
}

async function conectar() {
  if (await jaLogado()) return { ok: true, cookies: await coletarCookies() };
  const win = await chrome.windows.create({
    url: HUB, type: "popup", width: 520, height: 780, focused: true,
  });
  const tabId = win.tabs[0].id;

  return new Promise((resolve) => {
    let fim = false;
    let checando = false;

    const terminar = async (res) => {
      if (fim) return;
      fim = true;
      chrome.tabs.onUpdated.removeListener(aoAtualizar);
      chrome.windows.onRemoved.removeListener(aoFechar);
      clearTimeout(limite);
      if (res.ok) {
        res.cookies = await coletarCookies();
        chrome.windows.remove(win.id).catch(() => {});
      }
      resolve(res);
    };

    // confirma depois de um instante (o ML pode redirecionar para o login via JavaScript)
    const confirmar = async () => {
      if (checando || fim) return;
      checando = true;
      await new Promise((r) => setTimeout(r, 1500));
      try {
        const tab = await chrome.tabs.get(tabId);
        if (logadoUrl(tab.url) && tab.status === "complete") return terminar({ ok: true });
      } catch { /* janela fechada */ }
      checando = false;
    };

    const aoAtualizar = (id, info, tab) => {
      if (id === tabId && info.status === "complete" && logadoUrl(tab.url)) confirmar();
    };
    const aoFechar = (wid) => {
      if (wid === win.id) terminar({ ok: false, erro: "A janela do Mercado Livre foi fechada antes do login." });
    };
    const limite = setTimeout(
      () => terminar({ ok: false, erro: "Tempo esgotado. Tente conectar novamente." }), TEMPO_LIMITE);

    chrome.tabs.onUpdated.addListener(aoAtualizar);
    chrome.windows.onRemoved.addListener(aoFechar);
  });
}

// ---- busca de produtos no navegador da própria pessoa
// O Mercado Livre não entrega a página de resultados ao IP do servidor; no navegador da pessoa, com a
// conta dela, entrega normalmente. Aqui só lemos a página de resultados e mandamos os cartões dos produtos.
const POR_PAGINA = 48;
const MARCA = '"polycard":{';

function urlBusca(termo, pagina) {
  const slug = encodeURIComponent(termo.trim().replace(/\s+/g, "-"));
  return pagina === 0
    ? `https://lista.mercadolivre.com.br/${slug}`
    : `https://lista.mercadolivre.com.br/${slug}_Desde_${pagina * POR_PAGINA + 1}_NoIndex_True`;
}

// guarda só o começo de cada cartão (o suficiente para título, preço, link, vendas, nota e foto)
function cortarCartoes(html) {
  const t = html.replace(/\\\\"/g, '"').replace(/\\"/g, '"');  // o ML às vezes escapa o JSON
  const partes = t.split(MARCA).slice(1);
  return partes.map((p) => MARCA + p.slice(0, 6000)).join("\n");
}

async function buscarProdutos(termo, limite) {
  const paginas = Math.min(4, Math.ceil((limite + 10) / POR_PAGINA));
  let trechos = "", cartoes = 0;
  for (let pg = 0; pg < paginas; pg++) {
    const r = await fetch(urlBusca(termo, pg), { credentials: "include" });
    if (!r.ok || /captcha|account-verification|\/jms\/|login/.test(r.url)) {
      if (cartoes) break;
      return { ok: false, erro: "O Mercado Livre pediu uma verificação. Abra o Mercado Livre neste navegador, faça uma busca e tente de novo." };
    }
    const cortado = cortarCartoes(await r.text());
    const n = cortado ? cortado.split(MARCA).length - 1 : 0;
    if (!n) break;
    trechos += cortado + "\n";
    cartoes += n;
    if (cartoes >= limite + 10) break;
  }
  return cartoes ? { ok: true, trechos, cartoes } : { ok: false, erro: "Nenhum produto encontrado." };
}

chrome.runtime.onMessage.addListener((msg, _sender, responder) => {
  if (msg?.acao === "sincronizar") {
    coletarCookies().then((cookies) => responder({ ok: true, cookies }));
    return true;
  }
  if (msg?.acao === "buscar") {
    buscarProdutos(String(msg.termo || ""), Number(msg.limite) || 20)
      .then(responder, (e) => responder({ ok: false, erro: String(e?.message || e) }));
    return true;
  }
  if (msg?.acao === "conectar") {
    conectar().then(responder, (e) => responder({ ok: false, erro: String(e?.message || e) }));
    return true;
  }
});
