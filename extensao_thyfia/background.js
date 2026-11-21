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

async function conectar() {
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

chrome.runtime.onMessage.addListener((msg, _sender, responder) => {
  if (msg?.acao === "sincronizar") {
    coletarCookies().then((cookies) => responder({ ok: true, cookies }));
    return true;
  }
  if (msg?.acao === "conectar") {
    conectar().then(responder, (e) => responder({ ok: false, erro: String(e?.message || e) }));
    return true;
  }
});
