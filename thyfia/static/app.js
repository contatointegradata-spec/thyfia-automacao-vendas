/* =====================================================================
   ThyfIA — lógica da interface
   ===================================================================== */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const espera = (ms) => new Promise((r) => setTimeout(r, ms));
const REDUZIR = matchMedia("(prefers-reduced-motion: reduce)").matches;

const estado = {
  usuario: null,
  ml: "desconectado",
  view: "gerar",
  limite: 20,
  job: null,
  pollJob: null,
  pollMl: null,
  frasesTimer: null,
};

/* ------------------------------------------------------------------ API */
async function api(caminho, opcoes = {}) {
  const r = await fetch(caminho, {
    method: opcoes.method || (opcoes.body ? "POST" : "GET"),
    headers: opcoes.body ? { "Content-Type": "application/json" } : {},
    body: opcoes.body ? JSON.stringify(opcoes.body) : undefined,
    credentials: "same-origin",
  });
  let dados = null;
  try { dados = await r.json(); } catch { /* sem corpo */ }
  if (!r.ok) {
    let msg = dados?.detail || "Algo deu errado. Tente novamente.";
    if (Array.isArray(msg)) msg = "Verifique os campos e tente novamente.";
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return dados;
}

/* ------------------------------------------------------------------ toasts */
function toast(msg, tipo = "info") {
  const el = document.createElement("div");
  el.className = `toast ${tipo}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("sai"); el.addEventListener("animationend", () => el.remove()); }, 3800);
}

/* ------------------------------------------------------------------ ripple nos botões */
document.addEventListener("pointerdown", (e) => {
  const b = e.target.closest(".btn");
  if (!b || REDUZIR) return;
  const r = b.getBoundingClientRect();
  const s = Math.max(r.width, r.height);
  const c = document.createElement("span");
  c.className = "ripple";
  c.style.cssText = `width:${s}px;height:${s}px;left:${e.clientX - r.left - s / 2}px;top:${e.clientY - r.top - s / 2}px`;
  b.appendChild(c);
  c.addEventListener("animationend", () => c.remove());
});

function carregando(btn, sim) { btn.classList.toggle("carregando", sim); btn.disabled = sim; }

/* ------------------------------------------------------------------ partículas de fundo */
(function particulas() {
  const cv = $("#particulas"), ctx = cv.getContext("2d");
  let pts = [], w, h;
  const redim = () => {
    w = cv.width = innerWidth * devicePixelRatio; h = cv.height = innerHeight * devicePixelRatio;
    cv.style.width = innerWidth + "px"; cv.style.height = innerHeight + "px";
    const n = Math.min(70, Math.floor(innerWidth / 22));
    pts = Array.from({ length: n }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - .5) * .25 * devicePixelRatio, vy: (Math.random() - .5) * .25 * devicePixelRatio,
      r: (Math.random() * 1.4 + .4) * devicePixelRatio,
    }));
  };
  redim(); addEventListener("resize", redim);
  const passo = () => {
    ctx.clearRect(0, 0, w, h);
    const lim = 120 * devicePixelRatio;
    for (const p of pts) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
      ctx.fillStyle = "rgba(167,139,250,.55)";
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
    }
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      const a = pts[i], b = pts[j], d = Math.hypot(a.x - b.x, a.y - b.y);
      if (d < lim) {
        ctx.strokeStyle = `rgba(34,211,238,${(1 - d / lim) * .14})`;
        ctx.lineWidth = devicePixelRatio * .6;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
    }
    if (!REDUZIR) requestAnimationFrame(passo);
  };
  passo();
})();

/* ------------------------------------------------------------------ confete */
function confete(qtd = 140) {
  if (REDUZIR) return;
  const cv = $("#confete"), ctx = cv.getContext("2d");
  cv.width = innerWidth; cv.height = innerHeight;
  const cores = ["#8b5cf6", "#22d3ee", "#a78bfa", "#34d399", "#ffe600", "#f472b6"];
  const ps = Array.from({ length: qtd }, () => ({
    x: innerWidth / 2 + (Math.random() - .5) * 200, y: innerHeight / 2 - 40,
    vx: (Math.random() - .5) * 16, vy: Math.random() * -15 - 4,
    s: Math.random() * 7 + 4, r: Math.random() * 6, vr: (Math.random() - .5) * .3,
    c: cores[(Math.random() * cores.length) | 0], vida: 1,
  }));
  const passo = () => {
    ctx.clearRect(0, 0, cv.width, cv.height);
    let vivos = 0;
    for (const p of ps) {
      p.vy += .38; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.r += p.vr; p.vida -= .007;
      if (p.vida <= 0 || p.y > cv.height + 20) continue;
      vivos++;
      ctx.save(); ctx.globalAlpha = Math.min(1, p.vida * 1.5); ctx.translate(p.x, p.y); ctx.rotate(p.r);
      ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); ctx.restore();
    }
    if (vivos) requestAnimationFrame(passo); else ctx.clearRect(0, 0, cv.width, cv.height);
  };
  passo();
}

/* ------------------------------------------------------------------ contador animado */
function contar(el, alvo, dur = 1200, sufixo = "") {
  const ini = performance.now();
  const de = 0;
  const f = (t) => {
    const k = Math.min(1, (t - ini) / dur), e = 1 - Math.pow(1 - k, 4);
    el.textContent = Math.round(de + (alvo - de) * e).toLocaleString("pt-BR") + sufixo;
    if (k < 1) requestAnimationFrame(f);
  };
  requestAnimationFrame(f);
}

/* ------------------------------------------------------------------ telas */
function mostrarTela(id, efeito = "entrando", origem) {
  const tela = $(id);
  tela.hidden = false;
  if (origem) { tela.style.setProperty("--rx", origem.x + "px"); tela.style.setProperty("--ry", origem.y + "px"); }
  tela.classList.remove("entrando", "revelando", "saindo");
  void tela.offsetWidth;
  tela.classList.add(efeito);
  tela.addEventListener("animationend", () => tela.classList.remove(efeito), { once: true });
}
async function esconderTela(id) {
  const tela = $(id);
  if (tela.hidden) return;
  tela.classList.add("saindo");
  await espera(REDUZIR ? 0 : 550);
  tela.hidden = true; tela.classList.remove("saindo");
}

/* ------------------------------------------------------------------ inicialização */
async function iniciar() {
  const minimo = espera(REDUZIR ? 0 : 1700);
  let eu = null;
  try { eu = await api("/api/eu"); } catch { /* não logado */ }
  await minimo;
  $("#splash").classList.add("sumindo");
  setTimeout(() => $("#splash").remove(), 800);
  if (eu) entrarNoApp(eu); else mostrarAuth();
}

/* ------------------------------------------------------------------ AUTH */
function mostrarAuth() {
  // reinicia animações de entrada
  const card = $("#auth-card");
  card.classList.remove("sucesso", "tremendo");
  card.style.animation = "none"; void card.offsetWidth; card.style.animation = "";
  $$(".auth-form").forEach((f) => { f.reset(); $(".erro-form", f).textContent = ""; carregando($("button[type=submit]", f), false); });
  $(".forca span").style.width = "0";
  mostrarTela("#tela-auth");
}

function trocarAba(aba) {
  $(".auth-tabs").dataset.ativo = aba;
  $$(".auth-tab").forEach((t) => t.classList.toggle("ativo", t.dataset.tab === aba));
  $(".auth-trilho").classList.toggle("criar", aba === "criar");
  setTimeout(() => $(`#form-${aba === "criar" ? "cadastro" : "login"} input`).focus({ preventScroll: true }), 400);
}
$$(".auth-tab").forEach((t) => t.addEventListener("click", () => trocarAba(t.dataset.tab)));

$$(".olho").forEach((b) => b.addEventListener("click", () => {
  const i = b.previousElementSibling.previousElementSibling;
  i.type = i.type === "password" ? "text" : "password";
  b.textContent = i.type === "password" ? "👁" : "🙈";
}));

$("#form-cadastro [name=senha]").addEventListener("input", (e) => {
  const v = e.target.value;
  let p = 0;
  if (v.length >= 6) p++; if (v.length >= 10) p++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) p++; if (/\d/.test(v) && /\W/.test(v)) p++;
  const cores = ["#fb7185", "#fb7185", "#fbbf24", "#22d3ee", "#34d399"];
  const s = $(".forca span");
  s.style.width = v ? `${Math.max(12, p * 25)}%` : "0"; s.style.background = cores[p];
});

function erroAuth(form, msg) {
  $(".erro-form", form).textContent = msg;
  const card = $("#auth-card");
  card.classList.remove("tremendo"); void card.offsetWidth; card.classList.add("tremendo");
}

async function enviarAuth(e, url, campos) {
  e.preventDefault();
  const form = e.target, btn = $("button[type=submit]", form);
  const dados = Object.fromEntries(new FormData(form));
  $$(".campo", form).forEach((c) => c.classList.remove("invalido"));
  const vazios = campos.filter((c) => !String(dados[c] || "").trim());
  if (vazios.length) {
    vazios.forEach((c) => form.elements[c].closest(".campo").classList.add("invalido"));
    return erroAuth(form, "Preencha todos os campos.");
  }
  $(".erro-form", form).textContent = "";
  carregando(btn, true);
  try {
    const [eu] = await Promise.all([api(url, { body: dados }), espera(700)]);
    const r = btn.getBoundingClientRect();
    $("#auth-card").classList.add("sucesso");
    await espera(REDUZIR ? 0 : 450);
    await esconderTela("#tela-auth");
    entrarNoApp(eu, { x: r.left + r.width / 2, y: r.top + r.height / 2 });
  } catch (err) {
    carregando(btn, false);
    erroAuth(form, err.message);
  }
}
$("#form-login").addEventListener("submit", (e) => enviarAuth(e, "/api/login", ["email", "senha"]));
$("#form-cadastro").addEventListener("submit", (e) => enviarAuth(e, "/api/cadastro", ["nome", "email", "senha"]));

/* ------------------------------------------------------------------ APP */
function saudacao() {
  const h = new Date().getHours();
  return h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
}

function entrarNoApp(eu, origem) {
  estado.usuario = eu;
  const primeiro = eu.nome.split(" ")[0];
  $("#usuario-nome").textContent = eu.nome;
  $("#usuario-email").textContent = eu.email;
  $("#avatar").textContent = primeiro[0].toUpperCase();
  $("#saudacao").textContent = `${saudacao()}, ${primeiro}! ✦`;
  aplicarStatusMl(eu.ml);
  aplicarPreferencias(eu.preferencias);
  // reinicia a animação dos itens do menu
  $$(".menu-item").forEach((m) => { m.style.animation = "none"; void m.offsetWidth; m.style.animation = ""; });
  irPara("gerar", true);
  mostrarTela("#tela-app", origem ? "revelando" : "entrando", origem);
  requestAnimationFrame(moverIndicador);
  if (eu.job_ativo) { estado.job = eu.job_ativo.id; abrirGerando(eu.job_ativo); }
  if (eu.ml.status === "conectando") abrirModalMl(true);
  if (origem) setTimeout(() => toast(`Que bom te ver, ${primeiro}!`, "ok"), 700);
}

/* ---- menu e troca de views ---- */
function moverIndicador() {
  const ativo = $(".menu-item.ativo"), ind = $(".menu-ind");
  if (!ativo) return;
  ind.style.transform = `translateY(${ativo.offsetTop}px)`;
  ind.style.height = ativo.offsetHeight + "px";
}
addEventListener("resize", moverIndicador);

async function irPara(view, imediato = false) {
  if (view === estado.view && !imediato) return;
  const atual = $(`#view-${estado.view}`), nova = $(`#view-${view}`);
  $$(".menu-item").forEach((m) => m.classList.toggle("ativo", m.dataset.view === view));
  moverIndicador();
  if (!imediato && atual && !atual.hidden) {
    atual.classList.remove("entrando"); atual.classList.add("saindo");
    await espera(REDUZIR ? 0 : 200);
    atual.classList.remove("saindo");
  }
  $$(".view").forEach((v) => (v.hidden = v !== nova));
  estado.view = view;
  nova.classList.remove("entrando"); void nova.offsetWidth; nova.classList.add("entrando");
  $(".conteudo").scrollTo({ top: 0 });
  if (view === "historico") carregarHistorico();
  if (view === "cerebro") carregarCerebro();
  if (view === "publicacao") abrirPublicacao();
  if (view === "gerar") setTimeout(() => !$("#busca-card").classList.contains("liberado") || $("#input-termo").focus({ preventScroll: true }), 500);
}
$$(".menu-item").forEach((m) => m.addEventListener("click", () => irPara(m.dataset.view)));
document.addEventListener("click", (e) => { const b = e.target.closest("[data-ir]"); if (b) irPara(b.dataset.ir); });

/* ---- logout ---- */
$("#btn-sair").addEventListener("click", async () => {
  if (estado.pollJob) return toast("Aguarde a geração terminar para sair.", "info");
  const ov = $("#overlay-logout");
  $("#logout-txt").textContent = `Até logo, ${estado.usuario.nome.split(" ")[0]}!`;
  ov.hidden = false; ov.classList.remove("fechando");
  await Promise.all([api("/api/logout", { body: {} }).catch(() => {}), espera(REDUZIR ? 0 : 1600)]);
  pararPolls();
  estado.usuario = null;
  $("#tela-app").hidden = true;
  ov.classList.add("fechando");
  await espera(350);
  ov.hidden = true;
  mostrarAuth();
  trocarAba("entrar");
});

/* ------------------------------------------------------------------ MERCADO LIVRE */
function aplicarStatusMl(ml) {
  estado.ml = ml.status;
  const pill = $("#ml-pill"), desc = $("#ml-desc");
  const textos = {
    desconectado: ["desconectado", ml.mensagem || "Conecte sua conta para gerar links com a sua etiqueta de afiliado."],
    conectando: ["conectando…", "Finalize o login na janela do Chrome que abrimos."],
    conectado: ["conectado", "Sua conta está conectada. Os links são gerados com a sua etiqueta de afiliado."],
    erro: ["erro", ml.mensagem || "Não foi possível conectar."],
  };
  const [p, d] = textos[ml.status] || textos.desconectado;
  pill.textContent = p; pill.className = `status-pill ${ml.status}`; desc.textContent = d;
  const on = ml.status === "conectado";
  $("#btn-conectar-ml").hidden = on;
  $("#btn-desconectar-ml").hidden = !on;
  $("#btn-conectar-ml").textContent = ml.status === "erro" ? "Tentar novamente" : "Conectar Mercado Livre";
  $("#dot-ml").classList.toggle("on", on);
  $("#busca-card").classList.toggle("liberado", on);
}

function estadoModal(qual) {
  $$("#modal-ml .estado").forEach((e) => (e.hidden = !e.classList.contains(qual)));
}

/* ---- extensão ThyfIA Conector (login no próprio navegador) ---- */
const temExtensao = () => !!document.documentElement.dataset.thyfiaExt;

function falarComExtensao(acao, tempo = 6 * 60 * 1000) {
  return new Promise((resolve, reject) => {
    const id = Math.random().toString(36).slice(2);
    const ouvir = (e) => {
      if (e.source !== window || e.data?.origem !== "thyfia-ext" || e.data.id !== id) return;
      clearTimeout(t); removeEventListener("message", ouvir);
      e.data.ok ? resolve(e.data) : reject(new Error(e.data.erro || "A extensão não conseguiu conectar."));
    };
    const t = setTimeout(() => { removeEventListener("message", ouvir); reject(new Error("A extensão não respondeu.")); }, tempo);
    addEventListener("message", ouvir);
    postMessage({ origem: "thyfia-pagina", id, acao }, location.origin);
  });
}

async function sincronizarSessao() {
  if (!temExtensao()) return;
  try {
    const r = await falarComExtensao("sincronizar", 8000);
    if (r.cookies?.length) await api("/api/ml/sessao", { body: { cookies: r.cookies } });
  } catch { /* segue com a sessão já salva */ }
}

async function abrirModalMl(jaConectando = false) {
  const ov = $("#modal-ml");
  ov.hidden = false; ov.classList.remove("fechando");
  if (!jaConectando) {
    if (!temExtensao()) {
      api("/api/extensao").then((r) => ($("#ext-pasta").textContent = r.pasta)).catch(() => {});
      estadoModal("m-extensao");
      return;
    }
    return conectarViaExtensao(ov);
  }
  return conectarJanelaSeparada(ov, true);
}

async function conectarViaExtensao(ov) {
  estadoModal("m-aguardando");
  $("#ml-instrucao").innerHTML = "Abrimos o Mercado Livre numa janelinha <b>deste navegador</b>. Se você já estiver logado, a conexão é automática — senão, é só entrar na sua conta por lá.";
  $("#ml-msg").textContent = "Verificando seu login…";
  try {
    const r = await falarComExtensao("conectar");
    $("#ml-msg").textContent = "Login reconhecido! Salvando conexão…";
    aplicarStatusMl(await api("/api/ml/sessao", { body: { cookies: r.cookies } }));
    await sucessoMl(ov);
  } catch (e) {
    $("#ml-erro").textContent = e.message; estadoModal("m-erro");
  }
}

async function sucessoMl(ov) {
  estadoModal("m-ok"); confete(110);
  await espera(1900);
  fecharOverlay(ov);
  toast("Mercado Livre conectado!", "ok");
  irPara("gerar");
}

async function conectarJanelaSeparada(ov, jaConectando = false) {
  estadoModal("m-aguardando");
  $("#ml-instrucao").innerHTML = "Abrimos uma janela separada do Chrome. <b>Entre na sua conta do Mercado Livre por lá</b> — a ThyfIA detecta sozinha quando você terminar.";
  $("#ml-msg").textContent = "Abrindo o Chrome…";
  try {
    const st = jaConectando ? await api("/api/ml/status") : await api("/api/ml/conectar", { body: {} });
    aplicarStatusMl(st);
  } catch (e) {
    $("#ml-erro").textContent = e.message; estadoModal("m-erro"); return;
  }
  clearInterval(estado.pollMl);
  estado.pollMl = setInterval(async () => {
    let st;
    try { st = await api("/api/ml/status"); } catch { return; }
    aplicarStatusMl(st);
    if (st.status === "conectando") { $("#ml-msg").textContent = st.mensagem || "Aguardando login…"; return; }
    clearInterval(estado.pollMl); estado.pollMl = null;
    if (st.status === "conectado") {
      await sucessoMl(ov);
    } else {
      $("#ml-erro").textContent = st.mensagem || "Tente novamente."; estadoModal("m-erro");
    }
  }, 2000);
}

async function fecharOverlay(ov) {
  ov.classList.add("fechando");
  await espera(330);
  ov.hidden = true; ov.classList.remove("fechando");
}

$("#btn-conectar-ml").addEventListener("click", () => abrirModalMl());
$("#btn-tentar-ml").addEventListener("click", () => abrirModalMl());
$("#btn-recarregar").addEventListener("click", () => location.reload());
$("#btn-janela-separada").addEventListener("click", () => conectarJanelaSeparada($("#modal-ml")));
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".copiar");
  if (!b) return;
  const texto = b.dataset.copiar || $("#ext-pasta").textContent;
  try { await navigator.clipboard.writeText(texto); } catch { return toast("Não consegui copiar.", "erro"); }
  b.textContent = "copiado ✓"; b.classList.add("ok");
  setTimeout(() => { b.textContent = "copiar"; b.classList.remove("ok"); }, 1600);
});
["#btn-fechar-modal", "#btn-fechar-modal2"].forEach((s) => $(s).addEventListener("click", () => {
  fecharOverlay($("#modal-ml"));
  if (estado.ml === "conectando") toast("Continuamos aguardando seu login no Chrome.", "info");
}));
$("#btn-desconectar-ml").addEventListener("click", async (e) => {
  if (!confirm("Desconectar sua conta do Mercado Livre da ThyfIA?")) return;
  carregando(e.currentTarget, true);
  try { aplicarStatusMl(await api("/api/ml/desconectar", { body: {} })); toast("Conta desconectada.", "info"); }
  catch (err) { toast(err.message, "erro"); }
  carregando(e.currentTarget, false);
});

/* ------------------------------------------------------------------ BUSCA */
// placeholder digitando sozinho
(function placeholderAnimado() {
  const exemplos = ["fone bluetooth", "air fryer", "carregador turbo", "smartwatch", "cadeira gamer", "kit skincare", "tênis de corrida"];
  const el = $("#busca-ph");
  let i = 0, j = 0, apagando = false;
  const tick = () => {
    const pal = exemplos[i];
    el.textContent = "Ex.: " + pal.slice(0, j);
    if (!apagando && j++ >= pal.length) { apagando = true; return setTimeout(tick, 1500); }
    if (apagando && j-- <= 0) { apagando = false; i = (i + 1) % exemplos.length; j = 0; }
    setTimeout(tick, apagando ? 40 : 85);
  };
  tick();
})();

// limite (segmentado)
function moverThumb() {
  const seg = $("#seg-limite"), at = $("button.ativo", seg);
  $(".seg-thumb", seg).style.transform = `translateX(${at.offsetLeft - 4}px)`;
}
$$("#seg-limite button").forEach((b) => b.addEventListener("click", () => {
  $$("#seg-limite button").forEach((x) => x.classList.toggle("ativo", x === b));
  estado.limite = +b.dataset.v; moverThumb();
}));
new ResizeObserver(moverThumb).observe($("#seg-limite"));

// autocomplete aprendido
let tSug, focoSug = -1;
const input = $("#input-termo"), lista = $("#sugestoes");
async function buscarSugestoes() {
  const q = input.value.trim();
  try {
    const r = await api(`/api/cerebro/sugestoes?q=${encodeURIComponent(q)}`);
    const itens = r.sugestoes.filter((s) => s.termo !== q.toLowerCase());
    lista.innerHTML = "";
    if (itens.length) {
      lista.insertAdjacentHTML("beforeend", `<li class="s-titulo">${q ? "A ThyfIA sugere" : "Buscas populares"}</li>`);
      itens.forEach((s, k) => {
        const li = document.createElement("li");
        li.style.animationDelay = k * 35 + "ms";
        li.innerHTML = `<span class="s-ic">✦</span><span></span><small>${s.buscas} busca${s.buscas > 1 ? "s" : ""}${s.taxa != null ? ` · ${s.taxa}% ok` : ""}</small>`;
        li.children[1].textContent = s.termo;
        li.addEventListener("mousedown", (e) => { e.preventDefault(); escolher(s.termo); });
        lista.appendChild(li);
      });
    }
    lista.classList.toggle("aberta", itens.length > 0 && document.activeElement === input);
    focoSug = -1;
    const rel = $("#relacionados");
    rel.innerHTML = "";
    r.relacionados.forEach((t, k) => {
      const c = document.createElement("button");
      c.type = "button"; c.className = "chip"; c.textContent = t; c.style.animationDelay = k * 50 + "ms";
      c.addEventListener("click", () => escolher(t));
      rel.appendChild(c);
    });
  } catch { /* silencioso */ }
}
function escolher(t) { input.value = t; lista.classList.remove("aberta"); input.focus(); buscarSugestoes(); }
input.addEventListener("input", () => { clearTimeout(tSug); tSug = setTimeout(buscarSugestoes, 200); });
input.addEventListener("focus", buscarSugestoes);
input.addEventListener("blur", () => setTimeout(() => lista.classList.remove("aberta"), 120));
input.addEventListener("keydown", (e) => {
  const itens = $$("li:not(.s-titulo)", lista);
  if (!lista.classList.contains("aberta") || !itens.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    focoSug = (focoSug + (e.key === "ArrowDown" ? 1 : -1) + itens.length) % itens.length;
    itens.forEach((li, k) => li.classList.toggle("foco", k === focoSug));
  } else if (e.key === "Enter" && focoSug >= 0) {
    e.preventDefault(); escolher(itens[focoSug].children[1].textContent);
  } else if (e.key === "Escape") lista.classList.remove("aberta");
});

$("#form-busca").addEventListener("submit", async (e) => {
  e.preventDefault();
  const termo = input.value.trim();
  const btn = $(".btn-gerar");
  if (termo.length < 2) {
    const w = $(".busca-wrap"); w.classList.remove("tremendo"); void w.offsetWidth;
    w.animate([{ transform: "translateX(0)" }, { transform: "translateX(-8px)" }, { transform: "translateX(8px)" }, { transform: "translateX(0)" }], { duration: 350 });
    return toast("Digite a palavra-chave do produto.", "info");
  }
  lista.classList.remove("aberta");
  carregando(btn, true);
  try {
    await sincronizarSessao(); // usa a sessão mais recente do ML deste navegador
    const { id } = await api("/api/jobs", { body: { termo, limite: estado.limite } });
    estado.job = id;
    await espera(350);
    abrirGerando({ id, termo, status: "iniciando", total: 0, feitos: 0, ok: 0 });
  } catch (err) {
    toast(err.message, "erro");
    if (err.status === 400) irPara("integracoes");
  } finally { carregando(btn, false); }
});

/* ------------------------------------------------------------------ GERANDO */
const FRASES = [
  "Um café coado leva uns 4 minutos. Vamos ver quem termina primeiro ☕",
  "A ThyfIA está garimpando os produtos mais relevantes pra você.",
  "Cada busca deixa a ThyfIA mais esperta — para você e para a comunidade.",
  "Produtos que não aceitam afiliados ficam de fora. Só link que funciona!",
  "Dica: compartilhe os links no stories com uma foto do produto.",
  "Sua planilha já vem com títulos, preços e links clicáveis.",
  "Respira fundo, toma um gole… quase lá.",
];
const FASES = {
  iniciando: "Preparando o ambiente",
  coletando: "Procurando os melhores produtos",
  gerando: "Gerando seus links",
};

function estadoGerando(qual) {
  $$("#overlay-gerando .estado").forEach((e) => (e.hidden = !e.classList.contains(qual)));
}

function abrirGerando(job) {
  const ov = $("#overlay-gerando");
  ov.hidden = false; ov.classList.remove("fechando");
  estadoGerando("estado-progresso");
  $("#liquido").style.height = "8%";
  $("#prog-barra").style.width = "3%";
  // frases rotativas
  let i = 0;
  const fr = $("#frase-rotativa");
  fr.textContent = FRASES[0];
  clearInterval(estado.frasesTimer);
  estado.frasesTimer = setInterval(async () => {
    fr.classList.add("troca"); await espera(400);
    i = (i + 1) % FRASES.length; fr.textContent = FRASES[i]; fr.classList.remove("troca");
  }, 5200);
  atualizarGerando(job);
  clearInterval(estado.pollJob);
  estado.pollJob = setInterval(async () => {
    try { atualizarGerando(await api(`/api/jobs/${job.id}`)); } catch { /* tenta de novo */ }
  }, 1200);
}

function atualizarGerando(job) {
  const barra = $("#prog-barra");
  if (job.status === "gerando" && job.total) {
    const pct = Math.max(4, Math.round((job.feitos / job.total) * 100));
    barra.classList.remove("indeterminado");
    barra.style.width = pct + "%";
    $("#liquido").style.height = 8 + pct * 0.82 + "%";
    $("#prog-num").textContent = `${job.feitos} de ${job.total}`;
  } else if (job.status in FASES) {
    barra.classList.add("indeterminado");
    $("#prog-num").textContent = "";
  }
  if (FASES[job.status]) $("#prog-fase").textContent = FASES[job.status];
  if (job.status === "concluido") finalizarGerando(job);
  if (job.status === "erro") erroGerando(job);
}

function pararPolls() {
  clearInterval(estado.pollJob); estado.pollJob = null;
  clearInterval(estado.frasesTimer);
  clearInterval(estado.pollMl); estado.pollMl = null;
}

async function finalizarGerando(job) {
  pararPolls();
  const barra = $("#prog-barra");
  barra.classList.remove("indeterminado"); barra.style.width = "100%"; $("#liquido").style.height = "90%";
  await espera(REDUZIR ? 0 : 900);
  estadoGerando("estado-fim");
  $("#fim-resumo").textContent = `Busca: “${job.termo}”`;
  contar($("#fim-ok"), job.ok, 1100);
  contar($("#fim-total"), job.total, 900);
  $("#btn-baixar").href = `/api/jobs/${job.id}/planilha`;
  estado.jobFim = job;
  renderEnvioFim(job);
  if (job.envio_status === "enviando") acompanharEnvioAuto(job.id);
  confete();
}

/* ------------------------------------------------------------------ ENVIO PARA AUTOMAÇÃO (n8n) */
function resumoEnvio(job) {
  const r = job.envio_resumo || {};
  if (job.envio_status === "enviado") {
    const n = r.novos ?? 0, d = r.duplicados ?? 0;
    let t = `${n} ${n === 1 ? "novo" : "novos"}, ${d} ${d === 1 ? "duplicado" : "duplicados"}`;
    if (r.invalidos) t += `, ${r.invalidos} inválido${r.invalidos > 1 ? "s" : ""}`;
    return t;
  }
  if (job.envio_status === "erro") return job.envio_erro || "Falha no envio.";
  if (job.envio_status === "enviando") return "Enviando…";
  return "Ainda não enviado";
}

function confirmaReenvio(job) {
  if (job.envio_status !== "enviado") return true;
  return confirm(`Essa geração já foi enviada em ${dataBR(job.envio_em)} (${resumoEnvio(job)}).\n\n` +
    "Enviar de novo? É seguro: a automação ignora os links duplicados.");
}

async function enviarJob(job) {
  try {
    const atualizado = await api(`/api/jobs/${job.id}/enviar`, { body: {} });
    toast(`Enviado para a automação: ${resumoEnvio(atualizado)} ✔`, "ok");
    return atualizado;
  } catch (err) {
    toast(err.message, "erro");
    return { ...job, envio_status: "erro", envio_erro: err.message, envio_em: new Date().toISOString() };
  }
}

function renderEnvioFim(job) {
  const btn = $("#btn-enviar-auto"), msg = $("#fim-envio-msg");
  const semLinks = !(job.links ?? job.ok);  // links salvos no banco
  carregando(btn, job.envio_status === "enviando");
  btn.disabled = semLinks || job.envio_status === "enviando";
  $(".btn-txt", btn).textContent =
    job.envio_status === "erro" ? "↻ Tentar de novo" :
    job.envio_status === "enviado" ? "↻ Reenviar para automação" : "➜ Enviar para automação";
  msg.className = "envio-msg" + (job.envio_status === "enviado" ? " ok" : job.envio_status === "erro" ? " erro" : "");
  msg.textContent = semLinks ? "Geração sem links para enviar."
    : job.envio_status === "nao_enviado" ? "" : resumoEnvio(job);
}

$("#btn-enviar-auto").addEventListener("click", async () => {
  const job = estado.jobFim;
  if (!job || !confirmaReenvio(job)) return;
  renderEnvioFim({ ...job, envio_status: "enviando" });
  estado.jobFim = await enviarJob(job);
  renderEnvioFim(estado.jobFim);
});

// envio automático: o backend envia ao terminar; aqui só acompanhamos
async function acompanharEnvioAuto(id) {
  for (let i = 0; i < 60; i++) {
    await espera(1500);
    if (estado.jobFim?.id !== id) return;
    try {
      const j = await api(`/api/jobs/${id}`);
      if (j.envio_status !== "enviando") {
        estado.jobFim = j; renderEnvioFim(j);
        toast(j.envio_status === "enviado" ? `Enviado automaticamente: ${resumoEnvio(j)} ✔` : j.envio_erro || "Falha no envio.",
          j.envio_status === "enviado" ? "ok" : "erro");
        return;
      }
    } catch { /* tenta de novo */ }
  }
}

// preferência "enviar automaticamente"
function aplicarPreferencias(p = {}) {
  const chk = $("#chk-auto-envio"), lbl = chk.closest("label");
  chk.checked = !!p.auto_enviar;
  chk.disabled = p.automacao_configurada === false;
  lbl.style.opacity = chk.disabled ? ".5" : "";
  lbl.title = chk.disabled ? "Automação não configurada (defina N8N_WEBHOOK_URL e N8N_API_KEY no .env)"
    : "Envia os links para o n8n (WhatsApp/Telegram) assim que a geração terminar";
}
$("#chk-auto-envio").addEventListener("change", async (e) => {
  const quer = e.target.checked;
  try {
    aplicarPreferencias(await api("/api/preferencias", { body: { auto_enviar: quer } }));
    toast(quer ? "Envio automático ligado." : "Envio automático desligado.", "ok");
  } catch (err) { e.target.checked = !quer; toast(err.message, "erro"); }
});

function erroGerando(job) {
  pararPolls();
  estadoGerando("estado-erro");
  $("#erro-msg").textContent = job.erro || "Erro inesperado.";
  if ((job.erro || "").includes("Mercado Livre")) api("/api/ml/status").then(aplicarStatusMl).catch(() => {});
}

$("#btn-baixar").addEventListener("click", () => toast("Download iniciado! 📊", "ok"));
$("#btn-nova").addEventListener("click", async () => {
  await fecharOverlay($("#overlay-gerando"));
  input.value = ""; $("#relacionados").innerHTML = "";
  irPara("gerar", estado.view === "gerar"); input.focus();
});
$("#btn-erro-ok").addEventListener("click", async () => {
  await fecharOverlay($("#overlay-gerando"));
  if (estado.ml !== "conectado") irPara("integracoes");
});

/* ------------------------------------------------------------------ HISTÓRICO */
function dataBR(iso) {
  const d = new Date(iso.replace(" ", "T"));
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) + " · " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}
async function carregarHistorico() {
  const box = $("#lista-historico");
  box.innerHTML = '<div class="skeleton"></div>'.repeat(4);
  try {
    const [jobs] = await Promise.all([api("/api/jobs"), espera(REDUZIR ? 0 : 450)]);
    if (!jobs.length) {
      box.innerHTML = '<div class="vazio"><div>☕</div>Nenhuma geração ainda. Que tal a primeira?<br><br><button class="btn btn-primario" data-ir="gerar">Gerar links</button></div>';
      return;
    }
    box.innerHTML = "";
    jobs.forEach((j, k) => {
      const icone = { concluido: "✅", erro: "⚠️" }[j.status] || "⏳";
      const el = document.createElement("div");
      el.className = "h-item"; el.style.setProperty("--i", k);
      el.innerHTML = `
        <div class="h-ic">${icone}</div>
        <div><strong></strong><small>${dataBR(j.criado)}${j.status === "erro" ? " · " + (j.erro || "erro") : ""}</small></div>
        <div class="h-num">${j.ok}/${j.total}<small>links</small></div>
        <div class="h-envio"></div>
        <a class="h-baixar ${j.planilha ? "" : "off"}" href="/api/jobs/${j.id}/planilha" title="Baixar planilha" download>⬇</a>`;
      $("strong", el).textContent = j.termo;
      $("small", el).title = j.erro || "";
      if (j.status === "concluido") renderEnvioHist($(".h-envio", el), j);
      box.appendChild(el);
    });
  } catch (e) { box.innerHTML = ""; toast(e.message, "erro"); }
}

function renderEnvioHist(box, j) {
  const rotulos = { nao_enviado: "não enviado", enviando: "enviando…", enviado: "enviado", erro: "erro no envio" };
  box.innerHTML = `<span class="selo ${j.envio_status}"></span><small></small><button type="button"></button>`;
  $(".selo", box).textContent = rotulos[j.envio_status] || j.envio_status;
  const det = $("small", box);
  det.textContent = j.envio_status === "enviado" ? `${dataBR(j.envio_em)} · ${resumoEnvio(j)}`
    : j.envio_status === "erro" ? resumoEnvio(j) : "";
  det.title = det.textContent;
  const btn = $("button", box);
  btn.textContent = j.envio_status === "erro" ? "↻ Tentar de novo"
    : j.envio_status === "enviado" ? "↻ Reenviar" : "➜ Enviar para automação";
  const semLinks = !(j.links ?? j.ok);  // links salvos no banco
  btn.disabled = semLinks || j.envio_status === "enviando";
  if (semLinks) btn.title = "Geração sem links";
  btn.addEventListener("click", async () => {
    if (!confirmaReenvio(j)) return;
    btn.disabled = true; btn.textContent = "Enviando…";
    renderEnvioHist(box, await enviarJob(j));
  });
}

/* ------------------------------------------------------------------ AGENTE DE PUBLICAÇÃO */
const pub = { item: null, editando: null, foto: "", fotoLocal: "", fotoProduto: "", user: "achei_baratinho_oficial", ig: null };
const ROTULO_PUB = { rascunho: "rascunho", agendado: "agendado", publicando: "publicando…", publicado: "publicado", erro: "erro", cancelado: "cancelado" };

function abrirPublicacao() { statusInstagram(); carregarPalavras(); carregarPublicacoes(); }

async function statusInstagram(atualizar = false) {
  const conta = $("#ig-conta"), lim = $("#ig-limite"), box = $("#ig-status");
  box.className = "ig-status";
  try {
    const s = await api("/api/instagram/status" + (atualizar ? "?atualizar=true" : ""));
    pub.ig = s;
    $("#pub-aviso-txt").textContent = s.aviso;
    if (!s.configurado) {
      box.classList.add("off"); conta.textContent = "Instagram não configurado";
      lim.textContent = "Defina IG_ACCESS_TOKEN e IG_USER_ID no .env e reinicie o ThyfIA.";
    } else if (s.erro) {
      box.classList.add("erro"); conta.textContent = "Não consegui acessar a conta"; lim.textContent = s.erro;
    } else {
      box.classList.add("ok"); pub.user = s.username || pub.user;
      conta.textContent = "@" + pub.user;
      lim.textContent = s.limite ? `${s.limite.restante} de ${s.limite.total} publicações disponíveis nas próximas 24h` : "";
    }
    lim.title = lim.textContent;
    $("#pv-user").textContent = "@" + pub.user; $("#pv-user2").textContent = pub.user;
  } catch (e) { conta.textContent = "Erro ao verificar a conta"; lim.textContent = e.message; }
}
$("#btn-ig-atualizar").addEventListener("click", () => statusInstagram(true));

async function carregarPalavras() {
  try {
    const { palavras } = await api("/api/publicacao/palavras");
    const sel = $("#pub-keyword"), atual = sel.value;
    sel.innerHTML = '<option value="">Mais recente</option>';
    palavras.forEach((p) => { const o = document.createElement("option"); o.value = o.textContent = p; sel.appendChild(o); });
    sel.value = palavras.includes(atual) ? atual : "";
  } catch { /* sem itens ainda */ }
}

$("#btn-sugerir").addEventListener("click", async (e) => {
  const btn = e.currentTarget, box = $("#pub-candidatos"), info = $("#pub-info");
  carregando(btn, true); box.innerHTML = '<div class="skeleton"></div>'.repeat(3); info.textContent = "";
  try {
    const r = await api("/api/publicacao/sugerir", { body: { keyword: $("#pub-keyword").value || null, quantidade: +$("#pub-qtd").value || 5 } });
    box.innerHTML = "";
    if (!r.candidatos.length) {
      info.textContent = r.total_elegiveis ? "Nenhum candidato agora." :
        "Nenhum item pronto para publicar (precisa de título, preço e link de afiliado, e ainda não publicado). Gere links primeiro ou faça um post manual.";
      return;
    }
    info.textContent = `${r.total_elegiveis} itens elegíveis de “${r.keyword || "todas as buscas"}”.` +
      (r.sem_imagem ? ` ${r.sem_imagem} sem foto do produto: escolha e envie uma foto.` : "");
    r.candidatos.forEach((c, k) => {
      const el = document.createElement("article");
      el.className = "pub-card" + (k === 0 ? " melhor" : ""); el.style.setProperty("--i", k);
      el.innerHTML = `<div class="pub-card-img"></div><div class="pub-card-corpo">
        ${k === 0 ? '<span class="selo-melhor">★ melhor escolha</span>' : ""}
        <strong></strong><b class="pub-preco"></b><small class="pub-motivo"></small>
        <button class="btn btn-fantasma btn-mini">Escolher</button></div>`;
      const img = $(".pub-card-img", el);
      if (c.image_url) { const i = new Image(); i.src = c.image_url; i.alt = ""; i.loading = "lazy"; img.appendChild(i); }
      else img.innerHTML = '<span class="sem-foto">sem foto<br><small>envie uma</small></span>';
      $("strong", el).textContent = c.titulo; $(".pub-preco", el).textContent = c.preco_fmt;
      $(".pub-motivo", el).textContent = c.motivo;
      $("button", el).addEventListener("click", () => abrirEditor(c));
      box.appendChild(el);
    });
  } catch (err) { box.innerHTML = ""; toast(err.message, "erro"); }
  finally { carregando(btn, false); }
});

function horaPadrao() {
  const d = new Date(Date.now() + 60 * 60 * 1000); d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;
}

function isoParaLocal(iso) {
  const d = new Date(iso), z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;
}

function modoEdicao(p) {
  pub.editando = p ? p.id : null;
  $("#btn-agendar .btn-txt").textContent = p ? "💾 Salvar alterações" : "📅 Agendar publicação no Instagram";
  $("#btn-publicar-agora").hidden = !!p;
}

function editarAgendada(p) {
  abrirEditor(null);
  modoEdicao(p);
  pub.foto = p.image_url || ""; pub.fotoLocal = p.image_url && p.image_url.startsWith("/midia/") ? p.image_url : "";
  $("#pub-editor-titulo").textContent = "Alterar publicação agendada";
  $("#pub-legenda").value = p.legenda || "";
  $("#pub-quando").value = isoParaLocal(p.agendado_para);
  $("#pub-foto-msg").textContent = "Para trocar a foto, envie outra.";
  atualizarPreview();
}

function abrirEditor(c) {
  modoEdicao(null);
  pub.item = c;
  pub.fotoProduto = c?.image_url || "";
  pub.foto = pub.fotoProduto; pub.fotoLocal = "";
  $("#pub-editor-titulo").textContent = c ? "Revisar post" : "Post manual";
  $("#pub-legenda").value = c ? c.legenda : "";
  $("#pub-quando").value = horaPadrao();
  $("#pub-msg").textContent = ""; $("#pub-msg").className = "envio-msg";
  $("#pub-foto-msg").textContent = c && !c.image_url ? "Não consegui a foto deste produto pelo link. Envie uma foto." : "";
  $("#btn-foto-produto").hidden = true;
  $("#pub-arquivo").value = "";
  atualizarPreview();
  const ed = $("#pub-editor"); ed.hidden = false;
  ed.scrollIntoView({ behavior: REDUZIR ? "auto" : "smooth", block: "start" });
  if (!c) $("#pub-legenda").focus({ preventScroll: true });
}
$("#btn-post-manual").addEventListener("click", () => abrirEditor(null));
$("#btn-fechar-editor").addEventListener("click", () => { $("#pub-editor").hidden = true; pub.item = null; modoEdicao(null); });

function atualizarPreview() {
  const src = pub.fotoLocal || pub.foto, img = $("#pv-img");
  img.hidden = !src; $("#pv-sem-foto").hidden = !!src;
  if (src) img.src = src; else img.removeAttribute("src");
  const leg = $("#pub-legenda").value;
  $("#pv-legenda").textContent = leg;
  $("#pub-contador").textContent = `${leg.length}/2200`;
}
$("#pub-legenda").addEventListener("input", atualizarPreview);

$("#pub-arquivo").addEventListener("change", async (e) => {
  const f = e.target.files[0], msg = $("#pub-foto-msg");
  if (!f) return;
  if (f.size > 8 * 1024 * 1024) { msg.textContent = "A foto passa de 8 MB."; return; }
  msg.textContent = "Enviando foto…";
  try {
    const r = await fetch("/api/publicacao/imagem", { method: "POST", body: f, headers: { "Content-Type": f.type || "application/octet-stream" }, credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Não consegui enviar a foto.");
    pub.fotoLocal = d.local_url;
    pub.foto = d.image_url;  // foto local vira link público só na hora de publicar
    msg.textContent = d.aviso;
    msg.classList.remove("alerta");
    $("#btn-foto-produto").hidden = !pub.fotoProduto;
    atualizarPreview();
  } catch (err) { msg.textContent = err.message; }
});
$("#btn-foto-produto").addEventListener("click", () => {
  pub.foto = pub.fotoProduto; pub.fotoLocal = ""; $("#pub-foto-msg").textContent = ""; $("#btn-foto-produto").hidden = true; $("#pub-arquivo").value = ""; atualizarPreview();
});

function dadosPost() {
  return { item_id: pub.item ? pub.item.item_id : null, legenda: $("#pub-legenda").value, image_url: pub.foto };
}
function msgPub(texto, tipo) { const m = $("#pub-msg"); m.textContent = texto; m.className = "envio-msg " + (tipo || ""); }
function validarPost() {
  if (!pub.foto) { msgPub("Escolha uma foto: envie uma imagem.", "erro"); return false; }
  if (!$("#pub-legenda").value.trim()) { msgPub("Escreva a legenda.", "erro"); return false; }
  return true;
}

$("#btn-agendar").addEventListener("click", async (e) => {
  if (!validarPost()) return;
  const quando = $("#pub-quando").value;
  if (!quando) { msgPub("Escolha a data e a hora.", "erro"); return; }
  const btn = e.currentTarget; carregando(btn, true);
  try {
    if (pub.editando) {
      await api(`/api/publicacao/${pub.editando}`, { method: "PUT", body: { ...dadosPost(), item_id: null, agendado_para: quando } });
      toast("Publicação alterada! ✏️", "ok");
      modoEdicao(null);
    } else {
      await api("/api/publicacao/agendar", { body: { ...dadosPost(), agendado_para: quando } });
      toast("Publicação agendada! 📅", "ok");
    }
    msgPub(`Agendado para ${dataBR(quando)}. Deixe o ThyfIA rodando nesse horário.`, "ok");
    $("#pub-editor").hidden = true; $("#pub-candidatos").innerHTML = ""; $("#pub-info").textContent = "";
    carregarPublicacoes(); carregarPalavras();
  } catch (err) { msgPub(err.message, "erro"); }
  finally { carregando(btn, false); }
});

$("#btn-publicar-agora").addEventListener("click", async (e) => {
  if (!validarPost()) return;
  if (!confirm(`Publicar AGORA no Instagram @${pub.user}?\n\nO post sai imediatamente e fica visível para todos.`)) return;
  const btn = e.currentTarget; carregando(btn, true); msgPub("Publicando… (o Instagram pode levar até 1 minuto)");
  try {
    const p = await api("/api/publicacao/publicar-agora", { body: { ...dadosPost(), confirmar: true } });
    if (p.status === "publicado") { msgPub("Publicado no Instagram! 🎉", "ok"); toast("Post publicado! 🎉", "ok"); confete(90); $("#pub-editor").hidden = true; $("#pub-candidatos").innerHTML = ""; }
    else msgPub(p.erro || "Não foi possível publicar.", "erro");
    carregarPublicacoes(); statusInstagram(true);
  } catch (err) { msgPub(err.message, "erro"); }
  finally { carregando(btn, false); }
});

async function carregarPublicacoes() {
  const box = $("#pub-lista");
  try {
    const lista = await api("/api/publicacao");
    if (!lista.length) { box.innerHTML = '<p class="muted">Nenhuma publicação ainda.</p>'; return; }
    box.innerHTML = "";
    lista.forEach((p) => {
      const el = document.createElement("div");
      el.className = "pub-linha";
      el.innerHTML = `<div class="pub-thumb"></div><div class="pub-linha-txt"><strong></strong><small></small></div>
        <span class="selo ${p.status}"></span><span class="pub-linha-acao"></span>`;
      if (p.image_url) { const i = new Image(); i.src = p.image_url; i.alt = ""; i.loading = "lazy"; $(".pub-thumb", el).appendChild(i); }
      $("strong", el).textContent = p.titulo || (p.legenda || "Post manual").split("\n")[0];
      const quando = p.status === "publicado" ? "publicado " + dataBR(p.publicado_em) : p.agendado_para ? "para " + dataBR(p.agendado_para) : dataBR(p.criado_em);
      const det = $("small", el); det.textContent = quando + (p.erro ? " · " + p.erro : ""); det.title = det.textContent;
      $(".selo", el).textContent = ROTULO_PUB[p.status] || p.status;
      if (p.status === "agendado") {
        const ed = document.createElement("button"); ed.type = "button"; ed.textContent = "Editar";
        ed.addEventListener("click", () => editarAgendada(p));
        $(".pub-linha-acao", el).appendChild(ed);
        const b = document.createElement("button"); b.type = "button"; b.textContent = "Cancelar";
        b.addEventListener("click", async () => {
          if (!confirm("Cancelar esta publicação agendada?")) return;
          b.disabled = true;
          try { await api(`/api/publicacao/${p.id}/cancelar`, { body: {} }); toast("Agendamento cancelado.", "ok"); carregarPublicacoes(); carregarPalavras(); }
          catch (err) { toast(err.message, "erro"); b.disabled = false; }
        });
        $(".pub-linha-acao", el).appendChild(b);
      }
      box.appendChild(el);
    });
  } catch (e) { box.innerHTML = ""; toast(e.message, "erro"); }
}

/* ------------------------------------------------------------------ CÉREBRO */
async function carregarCerebro() {
  try {
    const s = await api("/api/cerebro/stats");
    $$("#stats strong").forEach((el) => contar(el, s[el.dataset.k] || 0));
    const anel = $("#anel-taxa");
    anel.style.strokeDashoffset = 327;
    requestAnimationFrame(() => setTimeout(() => (anel.style.strokeDashoffset = 327 - (327 * s.taxa) / 100), 200));
    contar($("#taxa-num"), s.taxa, 1400, "%");
    const box = $("#top-termos");
    if (!s.top.length) { box.innerHTML = '<p class="muted">Ainda aprendendo… faça a primeira busca!</p>'; return; }
    const max = Math.max(...s.top.map((t) => t.buscas));
    box.innerHTML = "";
    s.top.forEach((t, k) => {
      const el = document.createElement("div");
      el.className = "tt";
      el.innerHTML = `<div class="tt-linha"><span></span><small>${t.buscas} busca${t.buscas > 1 ? "s" : ""} · ${t.taxa}% ok</small></div><div class="tt-barra"><span></span></div>`;
      el.querySelector("span").textContent = t.termo;
      box.appendChild(el);
      setTimeout(() => (el.querySelector(".tt-barra span").style.width = (t.buscas / max) * 100 + "%"), 250 + k * 90);
    });
  } catch (e) { toast(e.message, "erro"); }
}

iniciar();
