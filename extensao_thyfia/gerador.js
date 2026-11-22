// Gera o link de afiliado de UM produto na página "Gerador de links" do Mercado Livre.
// Roda dentro dessa página (injetada pelo background.js), no navegador da própria pessoa: o ML recusa
// o gerador quando o pedido vem do IP do servidor, mas atende normalmente aqui.
// Devolve {ok: true, link} ou {ok: false, erro, detalhe}.
async function gerarNaPagina(urlProduto, tempoMs) {
  const RE = /https?:\/\/(?:meli\.la\/[A-Za-z0-9]+|(?:www\.)?mercadolivre\.com(?:\.br)?\/sec\/[A-Za-z0-9]+)/g;
  const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
  const resumo = () => (document.body ? document.body.innerText : "").replace(/\s+/g, " ").slice(0, 160);
  const achar = () => {
    let t = document.body ? document.body.innerText || "" : "";
    document.querySelectorAll("input, textarea").forEach((e) => { t += "\n" + (e.value || ""); });
    document.querySelectorAll('a[href*="/sec/"], a[href*="meli.la"]').forEach((a) => { t += "\n" + a.href; });
    return t.match(RE) || [];
  };
  const pedeLogin = () => /login|registration|account-verification|captcha|\/jms\//.test(location.href);

  if (pedeLogin()) return { ok: false, erro: "login", detalhe: location.href.slice(0, 100) };

  // campo onde se cola o link do produto
  let campo = null;
  for (let i = 0; i < 40 && !campo; i++) {
    campo = document.querySelector("textarea, input[type='url'], input[type='text'][placeholder*='link' i]");
    if (!campo) { if (pedeLogin()) return { ok: false, erro: "login" }; await dormir(500); }
  }
  if (!campo) return { ok: false, erro: "sem_campo", detalhe: resumo() };

  const antes = new Set(achar());
  // preenche como uma pessoa (o setter nativo + evento "input" faz o React perceber a mudança)
  const proto = campo.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  campo.focus();
  setter.call(campo, "");
  campo.dispatchEvent(new Event("input", { bubbles: true }));
  setter.call(campo, urlProduto);
  campo.dispatchEvent(new Event("input", { bubbles: true }));
  campo.dispatchEvent(new Event("change", { bubbles: true }));

  let botao = null;
  for (let i = 0; i < 24 && !botao; i++) {
    botao = [...document.querySelectorAll("button")].find((b) => /gerar/i.test(b.innerText || "") && !b.disabled);
    if (!botao) await dormir(250);
  }
  if (!botao) return { ok: false, erro: "sem_botao", detalhe: resumo() };
  botao.click();

  const fim = Date.now() + (tempoMs || 20000);
  while (Date.now() < fim) {
    const novos = achar().filter((l) => !antes.has(l));
    if (novos.length) return { ok: true, link: novos[novos.length - 1] };
    await dormir(400);
  }
  return { ok: false, erro: "sem_link", detalhe: resumo() };
}
