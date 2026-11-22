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
    const e = new Error(msg); e.status = r.status;
    if (r.status === 402 && typeof aoPlanoNecessario === "function") aoPlanoNecessario(msg);
    throw e;
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
  const DPR = Math.min(devicePixelRatio || 1, 1.5);  // tela retina não precisa de 2x aqui
  let pts = [], w, h, ultimo = 0;
  const redim = () => {
    w = cv.width = innerWidth * DPR; h = cv.height = innerHeight * DPR;
    cv.style.width = innerWidth + "px"; cv.style.height = innerHeight + "px";
    const n = Math.min(70, Math.floor(innerWidth / 22));
    pts = Array.from({ length: n }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - .5) * .25 * DPR, vy: (Math.random() - .5) * .25 * DPR,
      r: (Math.random() * 1.4 + .4) * DPR,
    }));
  };
  let parado = false;
  redim(); addEventListener("resize", () => { redim(); parado = false; });  // dentro do app as partículas ficam paradas (1 quadro); no login animam a ~30 fps
  const passo = (t = 0) => {
    if (!REDUZIR) requestAnimationFrame(passo);
    const noApp = !$("#tela-app").hidden;
    if (noApp && parado) return;
    if (document.hidden || (!noApp && t - ultimo < 33)) return;
    parado = noApp; ultimo = t;
    ctx.clearRect(0, 0, w, h);
    const lim = 120 * DPR;
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
        ctx.lineWidth = DPR * .6;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
    }
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
  $(".auth-tabs").dataset.abaAtiva = aba;
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
  aplicarPlano(eu);
  aplicarStatusMl(eu.ml);
  aplicarPreferencias(eu.preferencias);
  atualizarPassos();
  // reinicia a animação dos itens do menu
  $$(".menu-item").forEach((m) => { m.style.animation = "none"; void m.offsetWidth; m.style.animation = ""; });
  irPara("gerar", true);  // sem plano, o guarda do irPara leva para "Meu plano"
  mostrarTela("#tela-app", origem ? "revelando" : "entrando", origem);
  requestAnimationFrame(moverIndicador);
  if (eu.job_ativo) {
    estado.job = eu.job_ativo.id; abrirGerando(eu.job_ativo);
    // página recarregada no meio da geração: continua de onde parou (os links saem deste navegador)
    if (eu.job_ativo.status === "gerando" && temExtensao() && extensaoAtende("1.4.0")) gerarNaExtensao(eu.job_ativo.id);
  }
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
  const falta = typeof bloqueioDaView === "function" ? bloqueioDaView(view) : null;
  if (falta) {  // tela de um plano que o usuário não tem → mostra os planos, destacando o necessário
    plano.destaque = falta;
    if (view !== "plano" && estado.view !== "plano") toast("Esse recurso faz parte de outro plano. Veja as opções.", "info");
    view = "plano";
  }
  if (view === estado.view && !imediato) { if (view === "plano") carregarPlano(); return; }
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
  if (view === "ajuda") carregarAjuda();
  if (view === "plano") carregarPlano(falta ? falta : null);
  if (view === "automacao") abrirAutomacao();
  if (view === "piloto") abrirPiloto();
  if (view === "admin") abrirAdmin();
  if (view === "gerar") atualizarPassos();
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
// versão da extensão instalada (a página lê do atributo que a extensão coloca); "1.2.0" → [1,2,0]
const versaoExtensao = () => (document.documentElement.dataset.thyfiaExt || "0").split(".").map((n) => parseInt(n, 10) || 0);
const extensaoAtende = (minima) => {
  const v = versaoExtensao(), m = minima.split(".").map(Number);
  for (let i = 0; i < 3; i++) { if ((v[i] || 0) !== (m[i] || 0)) return (v[i] || 0) > (m[i] || 0); }
  return true;
};

function falarComExtensao(acao, tempo = 6 * 60 * 1000, dados) {
  return new Promise((resolve, reject) => {
    const id = Math.random().toString(36).slice(2);
    const ouvir = (e) => {
      if (e.source !== window || e.data?.origem !== "thyfia-ext" || e.data.id !== id) return;
      clearTimeout(t); removeEventListener("message", ouvir);
      e.data.ok ? resolve(e.data) : reject(new Error(e.data.erro || "A extensão não conseguiu conectar."));
    };
    const t = setTimeout(() => { removeEventListener("message", ouvir); reject(new Error("A extensão não respondeu.")); }, tempo);
    addEventListener("message", ouvir);
    postMessage({ origem: "thyfia-pagina", id, acao, dados }, location.origin);
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
  const dica = setTimeout(() => {
    $("#ml-msg").textContent = "Ainda aguardando… procure a janelinha do Mercado Livre (pode estar atrás desta janela) e entre na sua conta.";
  }, 20000);
  try {
    const r = await falarComExtensao("conectar");
    clearTimeout(dica);
    $("#ml-msg").textContent = "Login reconhecido! Salvando conexão…";
    aplicarStatusMl(await api("/api/ml/sessao", { body: { cookies: r.cookies } }));
    await sucessoMl(ov);
  } catch (e) {
    clearTimeout(dica);
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

// para qual grupo a busca vai: o Cérebro sugere, a pessoa pode trocar (só para quem tem o Piloto, plano Pro)
let nichoSeq = 0;
async function sugerirNicho() {
  const linha = $("#nicho-linha");
  if (estado.liberados && !estado.liberados.automacao) { linha.hidden = true; return; }
  const q = input.value.trim(), seq = ++nichoSeq;
  try {
    const r = await api(`/api/nicho-sugerido?busca=${encodeURIComponent(q)}`);
    if (seq !== nichoSeq) return;  // chegou uma resposta mais nova
    const sel = $("#sel-nicho"), atual = sel.value;
    if (sel.options.length !== r.catalogo.length + 1) {
      sel.innerHTML = `<option value="">Automático</option>` + r.catalogo.map((n) => `<option value="${esc(n.slug)}">${n.emoji} ${esc(n.nome)}</option>`).join("");
      sel.value = atual;
    }
    sel.options[0].textContent = r.nicho ? `Automático (${r.emoji} ${r.nome})` : "Automático";
    const dica = $("#nicho-dica");
    dica.classList.toggle("alerta", !!q && !r.nicho && !sel.value);
    dica.textContent = !q ? "" : sel.value ? "A oferta desta busca vai só para os grupos desse nicho."
      : r.nicho ? `O Cérebro entendeu esta busca como ${r.emoji} ${r.nome} e manda só para os grupos desse nicho.`
      : "Não reconheci o nicho desta busca. Escolha um para ela não ir só aos grupos “Todos os nichos”.";
    linha.hidden = false;
  } catch { /* silencioso: o campo é opcional */ }
}
$("#sel-nicho").addEventListener("change", sugerirNicho);

// autocomplete aprendido
let tSug, focoSug = -1;
const input = $("#input-termo"), lista = $("#sugestoes");
async function buscarSugestoes() {
  const q = input.value.trim();
  sugerirNicho();
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
// a escolha de grupo vale só para a busca em que foi feita: outro termo volta para "Automático"
function limparNichoEscolhido() { $("#sel-nicho").value = ""; }
function escolher(t) { limparNichoEscolhido(); input.value = t; lista.classList.remove("aberta"); input.focus(); buscarSugestoes(); }
input.addEventListener("input", () => { limparNichoEscolhido(); clearTimeout(tSug); tSug = setTimeout(buscarSugestoes, 200); });
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
    // a busca e a geração dos links rodam neste navegador (o ML recusa as duas quando vêm do IP do servidor)
    if (!temExtensao() || !extensaoAtende("1.4.0")) {
      const v = document.documentElement.dataset.thyfiaExt;
      throw new Error(v
        ? `Sua extensão ThyfIA Conector é a v${v}; agora ela precisa ser a v1.4.0 ou mais nova. Baixe de novo em Ajuda, troque a pasta e recarregue em chrome://extensions.`
        : "Instale a extensão ThyfIA Conector neste navegador (veja em Integrações) para gerar seus links.");
    }
    let trechos = null;
    try { trechos = (await falarComExtensao("buscar", 30000, { termo, limite: estado.limite })).trechos || null; }
    catch (e) { throw new Error(e.message || "Não consegui buscar os produtos neste navegador."); }
    const { id } = await api("/api/jobs", { body: { termo, limite: estado.limite, trechos, na_extensao: true, nicho: $("#sel-nicho").value } });
    estado.job = id;
    await espera(350);
    abrirGerando({ id, termo, status: "gerando", total: 0, feitos: 0, ok: 0 });
    gerarNaExtensao(id);
  } catch (err) {
    toast(err.message, "erro");
    if (err.status === 400) irPara("integracoes");
  } finally { carregando(btn, false); }
});

/* ------------------------------------------------------------------ GERAÇÃO NO NAVEGADOR (extensão) */
// Para cada produto, a extensão gera o link no gerador do ML (neste navegador) e a página entrega o
// resultado à ThyfIA. Se nada sair nos primeiros produtos, para logo em vez de esperar o resto.
async function gerarNaExtensao(id) {
  if (estado.gerandoExt === id) return;
  estado.gerandoExt = id;
  let ok = 0, seguidas = 0;
  try {
    const { urls } = await api(`/api/jobs/${id}/pendentes`);
    for (const url of urls) {
      let link = null;
      try { link = (await falarComExtensao("gerar", 60000, { url })).link || null; }
      catch (e) { await api(`/api/jobs/${id}/abortar`, { body: { erro: e.message } }).then(atualizarGerando); return; }
      const job = await api(`/api/jobs/${id}/links`, { body: { url, link } });
      atualizarGerando(job);
      if (job.status !== "gerando") return;
      if (link) { ok++; seguidas = 0; }
      else if (!ok && ++seguidas >= 4) {
        const erro = "O gerador de links do Mercado Livre não respondeu neste navegador. Abra o Mercado Livre, entre na sua conta e tente de novo.";
        return atualizarGerando(await api(`/api/jobs/${id}/abortar`, { body: { erro } }));
      }
    }
  } catch (e) {
    toast(e.message || "A geração foi interrompida.", "erro");
  } finally {
    estado.gerandoExt = null;
    falarComExtensao("gerar_fim", 4000).catch(() => {});
  }
}

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
  if (estado.fimDe === job.id) return;  // o acompanhamento e a geração podem avisar juntos
  estado.fimDe = job.id;
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
  const chk = $("#chk-auto-envio"), lbl = chk.closest("label"), chk2 = $("#chk-auto-envio2");
  const liberado = !estado.liberados || estado.liberados.automacao;
  chk.checked = !!p.auto_enviar; chk2.checked = !!p.auto_enviar;
  chk.disabled = chk2.disabled = p.automacao_configurada === false;
  lbl.hidden = !liberado;  // Plus não tem automação
  const fim = $("#fim-envio"); if (fim) fim.hidden = !liberado;
  lbl.style.opacity = chk.disabled ? ".5" : "";
  lbl.title = chk.disabled ? "Configure seu WhatsApp em Automação para ligar o envio automático"
    : "Envia os links para a automação (WhatsApp/Telegram) assim que a geração terminar";
  if (typeof au !== "undefined" && au.cfg && estado.view === "automacao") renderAutomacao();
}
async function mudarAutoEnvio(e) {
  const quer = e.target.checked;
  try {
    aplicarPreferencias(await api("/api/preferencias", { body: { auto_enviar: quer } }));
    toast(quer ? "Envio automático ligado." : "Envio automático desligado.", "ok");
  } catch (err) { e.target.checked = !quer; toast(err.message, "erro"); if (err.status === 409) irPara("automacao"); }
}
$("#chk-auto-envio").addEventListener("change", mudarAutoEnvio);
$("#chk-auto-envio2").addEventListener("change", mudarAutoEnvio);

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
    return lista;
  } catch (e) { if (!silencioso) { box.innerHTML = ""; toast(e.message, "erro"); } return null; }
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
  if (estado.liberados && !estado.liberados.automacao) { btn.hidden = true; $(".selo", box).hidden = true; }
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
const pub = { item: null, editando: null, foto: "", fotoLocal: "", fotoProduto: "", user: "sua_conta", ig: null,
  canal: null, formato: null, fotos: [], legendaEditada: false, canais: null, video: null, reelsFonte: "upload", fbNome: "" };
const MAX_CARROSSEL = 10;
/* catálogo da navegação: canal → formatos. "breve" = só a tela, ainda não publica */
const CANAIS_PUB = {
  instagram: { nome: "Instagram", icone: "IG", cor: "ig", desc: "Publique na sua conta comercial do Instagram.", formatos: {
    feed: { nome: "Feed", icone: "▣", desc: "Foto com legenda no seu perfil." },
    story: { nome: "Story", icone: "◔", desc: "Foto em tela cheia por 24 h, sem legenda." },
    carrossel: { nome: "Carrossel", icone: "▤", desc: "De 2 a 10 fotos que o público desliza." },
    reels: { nome: "Reels", icone: "▶", desc: "Vídeo vertical curto: seu MP4 ou gerado das fotos." } } },
  facebook: { nome: "Facebook", icone: "f", cor: "fb", desc: "Publique na sua Página do Facebook.", formatos: {
    feed: { nome: "Feed", icone: "▣", desc: "Foto com texto na linha do tempo da Página." },
    story: { nome: "Story", icone: "◔", desc: "Story da Página, em tela cheia." },
    publicacao: { nome: "Publicação com link", icone: "⛓", desc: "Post normal com o link do produto (clicável)." } } },
  whatsapp: { nome: "WhatsApp", icone: "✆", cor: "wa", desc: "Grupo, canal ou story — pela automação.", formatos: {
    grupo: { nome: "Grupo", icone: "☷", desc: "A oferta cai no seu grupo via automação.", via: true },
    canal: { nome: "Canal", icone: "☍", desc: "A oferta cai no seu canal via automação.", via: true },
    story: { nome: "Story (Status)", icone: "◔", desc: "Status do WhatsApp com a foto da oferta.", breve: true } } },
};
const PUBLICAVEL_PADRAO = ["instagram/feed", "instagram/story", "instagram/carrossel", "instagram/reels", "facebook/feed", "facebook/story", "facebook/publicacao"];
const usaBandeja = () => pub.canal === "instagram" && (pub.formato === "carrossel" || (pub.formato === "reels" && pub.reelsFonte === "auto"));
const nomeConta = () => (pub.canal === "facebook" ? `a Página ${pub.fbNome || "do Facebook"}` : `o Instagram @${pub.user}`);
const ehPublicavel = (c, f) => (pub.canais?.publicaveis || PUBLICAVEL_PADRAO).includes(`${c}/${f}`);
const ROTULO_FORMATO = { feed: "Feed", story: "Story", carrossel: "Carrossel", reels: "Reels", publicacao: "Publicação com link", grupo: "Grupo", canal: "Canal" };
const ROTULO_CANAL = { instagram: "Instagram", facebook: "Facebook", whatsapp: "WhatsApp" };
const ROTULO_PUB = { rascunho: "rascunho", agendado: "agendado", publicando: "publicando…", publicado: "publicado", erro: "erro", cancelado: "cancelado" };

async function abrirPublicacao() {
  carregarPublicacoes();
  try { pub.canais = await api("/api/publicacao/canais"); } catch { /* usa o padrão */ }
  mostrarNivelPub();
}

/* ---- navegação: canais → formatos → tela de trabalho ---- */
function escolherCanal(c) { pub.canal = c; pub.formato = null; fecharEditorPub(); mostrarNivelPub(); }
function escolherFormato(f) {
  pub.formato = f; pub.reelsFonte = "upload"; $('input[name="reels-fonte"][value="upload"]').checked = true;
  fecharEditorPub(); mostrarNivelPub();
  $(".conteudo").scrollTo({ top: 0, behavior: REDUZIR ? "auto" : "smooth" });
}
function voltarPub(nivel) {
  if (nivel === 0) { pub.canal = null; pub.formato = null; } else pub.formato = null;
  fecharEditorPub(); mostrarNivelPub();
}
function fecharEditorPub() { $("#pub-editor").hidden = true; pub.item = null; pub.fotos = []; pub.video = null; modoEdicao(null); }

function cartaoNav(icone, cor, nome, desc, selo, onclick, i) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "canal-card" + (cor ? " " + cor : ""); b.style.setProperty("--i", i);
  b.innerHTML = `<span class="canal-icone"></span><span class="canal-txt"><strong></strong><small></small></span><span class="canal-selo"></span>`;
  $(".canal-icone", b).textContent = icone; $("strong", b).textContent = nome; $("small", b).textContent = desc;
  const sl = $(".canal-selo", b);
  if (selo) { sl.textContent = selo.txt; sl.classList.add(selo.cls); } else sl.remove();
  b.addEventListener("click", onclick);
  return b;
}

function mostrarNivelPub() {
  const { canal, formato } = pub, c = canal && CANAIS_PUB[canal], f = c && formato && c.formatos[formato];
  const trilha = $("#pub-trilha");
  trilha.hidden = !canal; trilha.innerHTML = "";
  if (canal) {
    const t = (txt, fn) => { const e = document.createElement(fn ? "button" : "span"); e.textContent = txt; if (fn) { e.type = "button"; e.addEventListener("click", fn); } trilha.appendChild(e); };
    t("← Canais", () => voltarPub(0));
    t("›"); if (formato) { t(c.nome, () => voltarPub(1)); t("›"); t(f.nome); } else t(c.nome);
  }
  // nível 0: canais
  const gc = $("#pub-canais"); gc.hidden = !!canal; if (!canal) {
    gc.innerHTML = "";
    Object.entries(CANAIS_PUB).forEach(([k, v], i) => {
      let selo = null;
      if (k === "instagram" && pub.canais) selo = pub.canais.instagram.configurado ? { txt: "conectado", cls: "ok" } : { txt: "conectar", cls: "off" };
      if (k === "facebook" && pub.canais) selo = pub.canais.facebook.configurado ? { txt: "conectado", cls: "ok" } : { txt: "conectar", cls: "off" };
      if (k === "whatsapp" && pub.canais) selo = pub.canais.whatsapp.automacao_pronta ? { txt: "automação pronta", cls: "ok" } : { txt: "configurar", cls: "off" };
      gc.appendChild(cartaoNav(v.icone, v.cor, v.nome, v.desc, selo, () => escolherCanal(k), i));
    });
  }
  // nível 1: formatos
  const gf = $("#pub-formatos"); gf.hidden = !(canal && !formato); if (canal && !formato) {
    gf.innerHTML = "";
    Object.entries(c.formatos).forEach(([k, v], i) => {
      const selo = v.breve ? { txt: "em breve", cls: "breve" } : v.via ? { txt: "via automação", cls: "via" } : ehPublicavel(canal, k) ? { txt: "disponível", cls: "ok" } : null;
      gf.appendChild(cartaoNav(v.icone, c.cor, v.nome, v.desc, selo, () => escolherFormato(k), i));
    });
  }
  // nível 2: tela de trabalho
  const ws = (id, on) => { $("#" + id).hidden = !on; };
  const ig = canal === "instagram" && !!formato, fb = canal === "facebook" && !!formato;
  ws("pub-ws-instagram", ig);
  ws("pub-ws-facebook", fb);
  ws("pub-ws-editor", ig || (fb && !!pub.canais?.facebook?.configurado));
  ws("pub-ws-whatsapp", canal === "whatsapp" && !!formato);
  if (ig) { adaptarTelaFormato(); statusInstagram(); carregarPalavras(); }
  if (fb) { adaptarTelaFormato(); abrirFacebook(); }
  if (canal === "whatsapp" && formato) abrirWhatsapp();
}

/* textos e campos que mudam conforme o formato (feed / story / carrossel) */
function adaptarTelaFormato(manterSugestoes = false) {
  const { canal, formato: fm } = pub, fb = canal === "facebook";
  const story = fm === "story", reels = fm === "reels", link = fb && fm === "publicacao", bandeja = usaBandeja();
  $("#pub-campo-legenda").hidden = story;
  $("#pub-rotulo-legenda").textContent = link ? "Texto da publicação" : "Legenda";
  $("#pub-dica-legenda").hidden = story || link;
  $("#pub-dica-legenda").textContent = fb ? "No Facebook o link é clicável: você pode colar o link do produto no texto." : "O link de afiliado não vai na legenda (no Instagram ele não é clicável): use a bio e os canais.";
  $("#pub-dica-story").hidden = !story;
  $("#pub-campo-link").hidden = !link;
  $("#pub-reels").hidden = !reels;
  $("#pub-carrossel").hidden = !bandeja;
  $("#pub-foto-unica").hidden = bandeja || reels || link;
  $("#reels-upload").hidden = pub.reelsFonte !== "upload"; $("#reels-auto").hidden = pub.reelsFonte !== "auto";
  $("#reels-fonte").hidden = !!pub.editando;
  $("#pub-legenda").placeholder = link ? "Escreva o texto que vai acima do link…" : "Escreva a legenda do post…";
  const vertical = story || reels;
  $("#pv-img-box").classList.toggle("story", vertical); $(".ig-preview").classList.toggle("story", vertical);
  $("#carr-rotulo").textContent = reels ? "Fotos do vídeo" : "Fotos do carrossel";
  $("#carr-msg").innerHTML = reels ? "De 1 a 10 fotos, 3 segundos cada. Para incluir produtos, use “Sugerir” e clique em <b>Adicionar</b>."
    : "De 2 a 10 fotos. Para incluir produtos, use “Sugerir melhor item” e clique em <b>Adicionar</b>. A primeira foto é a capa; todas ficam quadradas.";
  $("#btn-post-manual").textContent = { story: "＋ Story manual com foto", carrossel: "＋ Carrossel manual com fotos", reels: "＋ Reels manual" }[fm] || (link ? "＋ Publicação manual com link" : "＋ Post manual com foto");
  $("#btn-sugerir .btn-txt").textContent = fm === "carrossel" || (reels && pub.reelsFonte === "auto") ? "✦ Sugerir itens" : "✦ Sugerir melhor item";
  if (!manterSugestoes) { $("#pub-info").textContent = ""; $("#pub-candidatos").innerHTML = ""; }
  $("#pv-user").textContent = "@" + (fb ? (pub.fbNome || "sua_pagina") : pub.user); $("#pv-user2").textContent = fb ? (pub.fbNome || "sua_pagina") : pub.user;
  modoEdicao(pub.editando ? { id: pub.editando } : null);
}

async function statusInstagram(atualizar = false) {
  const conta = $("#ig-conta"), lim = $("#ig-limite"), box = $("#ig-status");
  box.className = "ig-status";
  try {
    const s = await api("/api/instagram/status" + (atualizar ? "?atualizar=true" : ""));
    pub.ig = s;
    $("#pub-aviso-txt").textContent = s.aviso;
    if (!s.configurado) {
      box.classList.add("off"); conta.textContent = "Instagram não conectado";
      lim.textContent = "Siga o passo a passo abaixo para conectar a sua conta (leva uns 10 minutos).";
    } else if (s.erro) {
      box.classList.add("erro"); conta.textContent = "Não consegui acessar a conta"; lim.textContent = s.erro;
    } else {
      box.classList.add("ok"); pub.user = s.username || pub.user;
      conta.textContent = "@" + pub.user;
      lim.textContent = s.limite ? `${s.limite.restante} de ${s.limite.total} publicações disponíveis nas próximas 24h` : "";
    }
    lim.title = lim.textContent;
    ajustarTelaInstagram(s);
    $("#pv-user").textContent = "@" + pub.user; $("#pv-user2").textContent = pub.user;
  } catch (e) { conta.textContent = "Erro ao verificar a conta"; lim.textContent = e.message; }
}
$("#btn-ig-atualizar").addEventListener("click", () => statusInstagram(true));

async function carregarPalavras() {
  try {
    const { palavras } = await api("/api/publicacao/palavras?canal=" + (pub.canal || "instagram"));
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
    const r = await api("/api/publicacao/sugerir", { body: { keyword: $("#pub-keyword").value || null, quantidade: +$("#pub-qtd").value || 5, canal: pub.canal || "instagram" } });
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
        <button class="btn btn-fantasma btn-mini">${usaBandeja() ? "＋ Adicionar" : "Escolher"}</button></div>`;
      const img = $(".pub-card-img", el);
      if (c.image_url) { const i = new Image(); i.src = c.image_url; i.alt = ""; i.loading = "lazy"; img.appendChild(i); }
      else img.innerHTML = '<span class="sem-foto">sem foto<br><small>envie uma</small></span>';
      $("strong", el).textContent = c.titulo; $(".pub-preco", el).textContent = c.preco_fmt;
      $(".pub-motivo", el).textContent = c.motivo;
      $("button", el).addEventListener("click", () => abrirEditor(c));
      el._c = c;
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
  const nome = { story: "story", carrossel: "carrossel", reels: "Reels" }[pub.formato] || "publicação";
  $("#btn-agendar .btn-txt").textContent = p ? "💾 Salvar alterações" : `📅 Agendar ${nome} no ${ROTULO_CANAL[pub.canal] || "Instagram"}`;
  $("#btn-publicar-agora").hidden = !!p;
}

function editarAgendada(p) {
  if (pub.canal !== p.canal || pub.formato !== p.formato) { pub.canal = p.canal; pub.formato = p.formato; mostrarNivelPub(); }
  abrirEditor(null);
  modoEdicao(p);
  pub.foto = p.image_url || ""; pub.fotoLocal = p.image_url && p.image_url.startsWith("/midia/") ? p.image_url : "";
  if (p.formato === "carrossel") pub.fotos = (p.midias || []).map((m) => ({ url: m.url, local: m.url.startsWith("/midia/") ? m.url : "", item_id: m.item_id ?? null }));
  if (p.formato === "reels") { pub.video = { video_url: p.video_url, capa_url: p.image_url }; mostrarVideo(); }
  $("#pub-link").value = p.link || "";
  $("#pub-editor-titulo").textContent = "Alterar publicação agendada";
  $("#pub-legenda").value = p.legenda || ""; pub.legendaEditada = true;
  $("#pub-quando").value = isoParaLocal(p.agendado_para);
  $("#pub-foto-msg").textContent = p.formato === "reels" ? "" : "Para trocar a foto, envie outra.";
  $("#reels-msg").textContent = p.formato === "reels" ? "O vídeo não pode ser trocado: para outro vídeo, cancele e crie um novo Reels." : "";
  adaptarTelaFormato(); modoEdicao(p);
  atualizarPreview();
}

/* ---- carrossel: bandeja de fotos ---- */
function adicionarFoto(f) {
  if (pub.fotos.length >= MAX_CARROSSEL) { toast(`O carrossel aceita até ${MAX_CARROSSEL} fotos.`, "erro"); return false; }
  pub.fotos.push(f); return true;
}
function moverFoto(i, d) { const j = i + d; if (j < 0 || j >= pub.fotos.length) return; [pub.fotos[i], pub.fotos[j]] = [pub.fotos[j], pub.fotos[i]]; renderBandeja(); }
function renderBandeja() {
  const box = $("#carr-bandeja"); box.innerHTML = "";
  pub.fotos.forEach((f, i) => {
    const el = document.createElement("div"); el.className = "carr-foto";
    el.innerHTML = `<img alt=""><span class="carr-n"></span><span class="carr-acoes"><button type="button" data-a="e" aria-label="Mover para a esquerda">◀</button><button type="button" data-a="d" aria-label="Mover para a direita">▶</button><button type="button" data-a="x" aria-label="Remover foto">✕</button></span>`;
    $("img", el).src = f.local || f.url; $(".carr-n", el).textContent = i === 0 ? "capa" : i + 1;
    el.addEventListener("click", (e) => {
      const a = e.target.closest("button")?.dataset.a; if (!a) return;
      if (a === "x") { pub.fotos.splice(i, 1); renderBandeja(); } else moverFoto(i, a === "e" ? -1 : 1);
    });
    box.appendChild(el);
  });
  $("#pub-carr-cont").textContent = `${pub.fotos.length}/${MAX_CARROSSEL}`;
  legendaDoCarrossel(); atualizarPreview();
}
/* legenda automática do carrossel: lista os produtos escolhidos, até o usuário editar à mão */
function legendaDoCarrossel() {
  if (!usaBandeja() || pub.legendaEditada) return;
  const itens = pub.fotos.filter((f) => f.titulo);
  if (!itens.length) return;
  $("#pub-legenda").value = ("🔥 Achados com preço bom!\n\n" + itens.map((f) => `• ${f.titulo.slice(0, 70)} — ${f.preco_fmt}`).join("\n") +
    "\n\n👉 Link na bio e nos nossos canais.\n\n#promoção #oferta #desconto #achadinhos").slice(0, 2200);
  atualizarContador();
}
function atualizarContador() { $("#pub-contador").textContent = `${$("#pub-legenda").value.length}/2200`; }
$("#pub-arquivos").addEventListener("change", async (e) => {
  const arqs = [...e.target.files], msg = $("#carr-msg"); e.target.value = "";
  for (const f of arqs) {
    if (pub.fotos.length >= MAX_CARROSSEL) { msg.textContent = `Máximo de ${MAX_CARROSSEL} fotos: as demais foram ignoradas.`; break; }
    if (f.size > 8 * 1024 * 1024) { msg.textContent = `“${f.name}” passa de 8 MB e foi ignorada.`; continue; }
    msg.textContent = `Enviando ${f.name}…`;
    try {
      const r = await fetch("/api/publicacao/imagem", { method: "POST", body: f, headers: { "Content-Type": f.type || "application/octet-stream" }, credentials: "same-origin" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Não consegui enviar a foto.");
      adicionarFoto({ url: d.image_url, local: d.local_url, item_id: null });
      renderBandeja(); msg.textContent = "";
    } catch (err) { msg.textContent = err.message; }
  }
});

function abrirEditor(c) {
  const carr = usaBandeja(), acumula = carr && !$("#pub-editor").hidden && !pub.editando && c;
  modoEdicao(null);
  pub.item = carr ? null : c;
  pub.fotoProduto = c?.image_url || "";
  pub.foto = pub.fotoProduto; pub.fotoLocal = "";
  if (!acumula) { pub.video = null; $("#reels-msg").textContent = ""; $("#reels-duracao").textContent = ""; $("#pub-link").value = ""; }
  if (carr) {
    if (!acumula) { pub.fotos = []; pub.legendaEditada = false; }
    if (c) {
      if (!c.image_url) { toast("Este produto está sem foto: envie uma foto pelo botão “Adicionar fotos”.", "erro"); }
      else if (pub.fotos.some((f) => f.item_id === c.item_id)) toast("Este produto já está no vídeo/carrossel.", "erro");
      else adicionarFoto({ url: c.image_url, local: "", item_id: c.item_id, titulo: c.titulo, preco_fmt: c.preco_fmt });
    }
    renderBandeja();
  }
  if (c && pub.canal === "facebook" && pub.formato === "publicacao") $("#pub-link").value = c.link_afiliado || "";
  $("#pub-editor-titulo").textContent = { story: c ? "Revisar story" : "Story manual", carrossel: "Montar carrossel", reels: "Montar Reels" }[pub.formato]
    || (pub.formato === "publicacao" ? "Publicação com link" : c ? "Revisar post" : "Post manual");
  if (!acumula) { $("#pub-legenda").value = c && !carr ? c.legenda : ""; pub.legendaEditada = false; if (carr) legendaDoCarrossel(); }
  if (!acumula) $("#pub-quando").value = horaPadrao();
  $("#pub-msg").textContent = ""; $("#pub-msg").className = "envio-msg";
  $("#pub-foto-msg").textContent = c && !c.image_url ? "Não consegui a foto deste produto pelo link. Envie uma foto." : "";
  $("#btn-foto-produto").hidden = true;
  $("#pub-arquivo").value = "";
  adaptarTelaFormato(true);
  atualizarPreview();
  const ed = $("#pub-editor"); ed.hidden = false;
  ed.scrollIntoView({ behavior: REDUZIR ? "auto" : "smooth", block: "start" });
  if (!c && pub.formato === "feed") $("#pub-legenda").focus({ preventScroll: true });
}
$("#btn-post-manual").addEventListener("click", () => abrirEditor(null));
$("#btn-fechar-editor").addEventListener("click", fecharEditorPub);

function mostrarVideo() {
  const v = $("#pv-video"), url = pub.video?.video_url;
  if (url) { if (v.getAttribute("src") !== url) v.src = url; } else { v.pause?.(); v.removeAttribute("src"); v.load?.(); }
  $("#reels-duracao").textContent = pub.video?.duracao ? `· ${pub.video.duracao}s` : "";
}
function atualizarPreview() {
  const carr = usaBandeja(), reels = pub.formato === "reels", img = $("#pv-img");
  const src = reels ? "" : carr ? (pub.fotos[0] && (pub.fotos[0].local || pub.fotos[0].url)) : (pub.fotoLocal || pub.foto);
  const temVideo = reels && !!pub.video;
  if (reels) mostrarVideo();
  $("#pv-video").hidden = !temVideo;
  img.hidden = !src; $("#pv-sem-foto").hidden = temVideo || !!src;
  if (src) img.src = src; else img.removeAttribute("src");
  const link = pub.canal === "facebook" && pub.formato === "publicacao";
  $("#pv-sem-foto").innerHTML = reels ? "Sem vídeo<br><small>envie ou gere o vídeo ao lado</small>"
    : link ? "🔗 O Facebook monta a prévia<br><small>com a foto e o título do link</small>" : "Sem foto<br><small>envie uma imagem ao lado</small>";
  const cont = $("#pv-contagem"); cont.hidden = !(pub.formato === "carrossel" && pub.fotos.length); cont.textContent = `1/${pub.fotos.length}`;
  const leg = $("#pub-legenda").value;
  $("#pv-legenda").textContent = pub.formato === "story" ? "" : leg;
  atualizarContador();
}
$("#pub-legenda").addEventListener("input", () => { pub.legendaEditada = true; atualizarPreview(); });

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

function msgReels(t, tipo) { const m = $("#reels-msg"); m.textContent = t; m.className = "mini" + (tipo ? " " + tipo : ""); }
$$('input[name="reels-fonte"]').forEach((r) => r.addEventListener("change", () => {
  pub.reelsFonte = r.value; pub.video = null; pub.fotos = []; pub.legendaEditada = false; renderBandeja(); adaptarTelaFormato(true); atualizarPreview();
}));
$("#pub-video-arquivo").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  if (f.size > 100 * 1024 * 1024) { msgReels("O vídeo passa de 100 MB.", "erro"); return; }
  msgReels("Enviando e preparando o vídeo… (pode levar até um minuto)");
  try {
    const r = await fetch("/api/publicacao/video", { method: "POST", body: f, headers: { "Content-Type": f.type || "application/octet-stream" }, credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Não consegui enviar o vídeo.");
    pub.video = d; msgReels("Vídeo pronto! Confira a prévia ao lado.", "ok"); atualizarPreview();
  } catch (err) { msgReels(err.message, "erro"); }
});
$("#btn-gerar-video").addEventListener("click", async (e) => {
  if (!pub.fotos.length) { msgReels("Escolha pelo menos 1 foto (use “Sugerir” e clique em Adicionar, ou envie fotos).", "erro"); return; }
  const btn = e.currentTarget; carregando(btn, true); msgReels("Montando o vídeo…");
  try {
    pub.video = await api("/api/publicacao/reels/gerar", { body: { midias: pub.fotos.map((f) => ({ url: f.url, item_id: f.item_id })), com_preco: $("#reels-com-preco").checked } });
    msgReels("Vídeo pronto! Confira a prévia ao lado.", "ok"); atualizarPreview();
  } catch (err) { msgReels(err.message, "erro"); }
  finally { carregando(btn, false); }
});

function dadosPost() {
  const canal = pub.canal || "instagram", formato = pub.formato || "feed";
  const base = { canal, formato, item_id: pub.item ? pub.item.item_id : null };
  const legenda = $("#pub-legenda").value;
  if (canal === "instagram" && formato === "carrossel") return { ...base, item_id: null, legenda, image_url: "", midias: pub.fotos.map((f) => ({ url: f.url, item_id: f.item_id })) };
  if (formato === "reels") return { ...base, legenda, image_url: pub.video?.capa_url || "", video_url: pub.video?.video_url };
  if (formato === "story") return { ...base, legenda: "", image_url: pub.foto };
  if (canal === "facebook" && formato === "publicacao") return { ...base, legenda, image_url: "", link: $("#pub-link").value.trim() };
  return { ...base, legenda, image_url: pub.foto };
}
function msgPub(texto, tipo) { const m = $("#pub-msg"); m.textContent = texto; m.className = "envio-msg " + (tipo || ""); }
function validarPost() {
  const { canal, formato } = pub;
  if (formato === "reels") {
    if (!pub.video) { msgPub(pub.reelsFonte === "auto" ? "Gere o vídeo com as fotos antes de publicar." : "Envie o vídeo antes de publicar.", "erro"); return false; }
  } else if (canal === "instagram" && formato === "carrossel") {
    if (pub.fotos.length < 2) { msgPub("O carrossel precisa de pelo menos 2 fotos.", "erro"); return false; }
  } else if (canal === "facebook" && formato === "publicacao") {
    if (!/^https?:\/\/\S{4,}/.test($("#pub-link").value.trim())) { msgPub("Informe o link do produto (começando com https://).", "erro"); return false; }
  } else if (!pub.foto) { msgPub("Escolha uma foto: envie uma imagem.", "erro"); return false; }
  if (formato !== "story" && !$("#pub-legenda").value.trim()) { msgPub("Escreva o texto da publicação.", "erro"); return false; }
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
    fecharEditorPub(); $("#pub-candidatos").innerHTML = ""; $("#pub-info").textContent = "";
    carregarPublicacoes(); carregarPalavras();
  } catch (err) { msgPub(err.message, "erro"); }
  finally { carregando(btn, false); }
});

$("#btn-publicar-agora").addEventListener("click", async (e) => {
  if (!validarPost()) return;
  const oque = { story: "O story", carrossel: "O carrossel", reels: "O Reels" }[pub.formato] || "O post";
  if (!confirm(`Publicar AGORA n${nomeConta()}?\n\n${oque} sai imediatamente e fica visível para todos.`)) return;
  const btn = e.currentTarget; carregando(btn, true);
  msgPub(pub.formato === "reels" ? "Enviando o Reels…" : pub.formato === "carrossel" ? "Publicando o carrossel… (cada foto é processada; pode levar alguns minutos)" : "Publicando… (pode levar até 1 minuto)");
  try {
    const p = await api("/api/publicacao/publicar-agora", { body: { ...dadosPost(), confirmar: true } });
    if (p.status === "publicado") { msgPub(`Publicado com sucesso! 🎉`, "ok"); toast("Publicado! 🎉", "ok"); confete(90); fecharEditorPub(); $("#pub-candidatos").innerHTML = ""; }
    else if (p.status === "publicando") { toast("Reels enviado! O Instagram está processando o vídeo — acompanhe em Publicações.", "ok"); fecharEditorPub(); $("#pub-candidatos").innerHTML = ""; }
    else msgPub(p.erro || "Não foi possível publicar.", "erro");
    carregarPublicacoes(); if (pub.canal === "instagram") statusInstagram(true);
  } catch (err) { msgPub(err.message, "erro"); }
  finally { carregando(btn, false); }
});

/* acompanha publicações em andamento (Reels leva minutos): atualiza a lista até terminarem */
let pubPoll = null;
function acompanharPublicando() {
  if (pubPoll) return;
  let n = 0;
  pubPoll = setInterval(async () => {
    n++;
    const lista = await carregarPublicacoes(true);
    if (estado.view !== "publicacao" || n > 120 || !(lista || []).some((x) => x.status === "publicando")) { clearInterval(pubPoll); pubPoll = null; }
  }, 5000);
}

async function carregarPublicacoes(silencioso = false) {
  const box = $("#pub-lista");
  try {
    const lista = await api("/api/publicacao");
    if (lista.some((x) => x.status === "publicando")) acompanharPublicando();
    if (!lista.length) { box.innerHTML = '<p class="muted">Nenhuma publicação ainda.</p>'; return lista; }
    box.innerHTML = "";
    lista.forEach((p) => {
      const el = document.createElement("div");
      el.className = "pub-linha";
      el.innerHTML = `<div class="pub-thumb"></div><div class="pub-linha-txt"><strong></strong><small></small></div>
        <span class="selo ${p.status}"></span><span class="pub-linha-acao"></span>`;
      if (p.image_url) { const i = new Image(); i.src = p.image_url; i.alt = ""; i.loading = "lazy"; $(".pub-thumb", el).appendChild(i); }
      const resumo = p.titulo || (p.legenda || "").split("\n")[0] || "Post manual";
      $("strong", el).textContent = p.formato === "story" ? "Story" + (p.titulo ? " · " + p.titulo : "")
        : p.formato === "carrossel" ? `Carrossel de ${(p.midias || []).length} fotos` : p.formato === "reels" ? "Reels · " + resumo : resumo;
      const onde = `${ROTULO_CANAL[p.canal] || "Instagram"} ${ROTULO_FORMATO[p.formato] || "Feed"} · `;
      const quando = onde + (p.status === "publicado" ? "publicado " + dataBR(p.publicado_em) : p.agendado_para ? "para " + dataBR(p.agendado_para) : dataBR(p.criado_em));
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

/* ---- Facebook: conectar a Página (ID + token da Página) ---- */
async function abrirFacebook() {
  try { renderFacebook((await api("/api/publicacao/canais")).facebook); } catch (e) { $("#fb-detalhe").textContent = e.message; }
}
function renderFacebook(s) {
  const box = $("#fb-status"); box.className = "ig-status " + (s.configurado ? "ok" : "off");
  $("#fb-conta").textContent = s.configurado ? (s.nome || "Página conectada") : "Facebook não conectado";
  $("#fb-detalhe").textContent = s.configurado ? "Página validada pelo Facebook. O token está guardado criptografado." : "Conecte a sua Página para publicar nela.";
  $("#fb-conectar").hidden = !!s.configurado; $("#btn-fb-trocar").hidden = !s.configurado; $("#btn-fb-remover").hidden = !s.configurado;
  pub.fbNome = s.nome || "";
  if (pub.canais) pub.canais.facebook = { configurado: s.configurado, nome: s.nome || "" };
  if (pub.canal === "facebook" && pub.formato) {
    const editor = $("#pub-ws-editor"), era = editor.hidden;
    editor.hidden = !s.configurado;
    if (s.configurado) { ["pub-controles", "pub-info", "pub-candidatos"].forEach((id) => ($("#" + id).hidden = false)); if (era) { adaptarTelaFormato(); carregarPalavras(); } }
  }
}
$("#btn-fb-trocar").addEventListener("click", () => { $("#fb-conectar").hidden = false; $("#btn-fb-trocar").hidden = true; $("#btn-fb-remover").hidden = false; $("#fb-in-id").focus(); });
$("#fb-olho").addEventListener("click", () => { const i = $("#fb-in-token"); i.type = i.type === "password" ? "text" : "password"; });
$("#form-fb").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#btn-fb-conectar"), msg = $("#fb-msg"), id = $("#fb-in-id").value.trim(), token = $("#fb-in-token").value.trim();
  msg.className = "envio-msg";
  if (!id || !token) { msg.textContent = "Preencha o ID da Página e o token."; msg.classList.add("erro"); return; }
  carregando(btn, true); msg.textContent = "Perguntando ao Facebook se está tudo certo…";
  try {
    const r = await api("/api/conta/facebook", { method: "PUT", body: { page_id: id, token } });
    $("#fb-in-token").value = ""; msg.textContent = "";
    renderFacebook(r); toast("Página do Facebook conectada! ✓", "ok");
  } catch (err) { msg.textContent = err.message; msg.classList.add("erro"); }
  finally { carregando(btn, false); }
});
$("#btn-fb-remover").addEventListener("click", async () => {
  if (!confirm("Desconectar a Página do Facebook? Posts já agendados nela não serão afetados.")) return;
  try { renderFacebook(await api("/api/conta/facebook", { method: "DELETE" })); toast("Página desconectada.", "ok"); } catch (err) { toast(err.message, "erro"); }
});

/* ---- WhatsApp: grupo e canal saem pela automação ---- */
function abrirWhatsapp() {
  const f = CANAIS_PUB.whatsapp.formatos[pub.formato], w = pub.canais?.whatsapp || {};
  $("#wa-titulo").textContent = `WhatsApp · ${f.nome}`;
  $("#wa-selo").textContent = f.breve ? "em breve" : "via automação"; $("#wa-selo").className = "badge-breve" + (f.breve ? "" : " via");
  const info = $("#wa-estado"); info.innerHTML = "";
  const btn = $("#btn-wa-automacao"); btn.hidden = !!f.breve;
  if (f.breve) {
    $("#wa-txt").textContent = "O Status (story) do WhatsApp publica com a foto da oferta. Ele sai pelo número de cada cliente, por isso cada um vai conectar o próprio WhatsApp (leitura de QR) — essa parte está em construção.";
    return;
  }
  $("#wa-txt").textContent = pub.formato === "grupo"
    ? "Cada oferta com link gerado é enviada automaticamente para o seu grupo de WhatsApp. Você não publica à mão: a automação cuida do envio no ritmo que você configurar."
    : "Cada oferta com link gerado é enviada automaticamente para o seu canal de WhatsApp. Você não publica à mão: a automação cuida do envio no ritmo que você configurar.";
  const li = (ok, txt) => { const p = document.createElement("p"); p.className = "wa-linha " + (ok ? "ok" : "off"); p.textContent = (ok ? "✓ " : "• ") + txt; info.appendChild(p); };
  li(!!w.automacao_pronta, w.automacao_pronta ? "Automação pronta para enviar." : "Automação ainda não configurada — abra a Automação e preencha o destino.");
  li(!!w.numero, w.numero ? `WhatsApp de destino: ${w.numero}` : "Nenhum número/grupo de destino informado.");
  if (w.telegram) li(true, `Telegram: ${w.telegram}`);
}
$("#btn-wa-automacao").addEventListener("click", () => irPara("automacao"));

/* ------------------------------------------------------------------ CÉREBRO */
async function carregarCerebro() {
  if (typeof carregarAprendizado === "function") carregarAprendizado();
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


/* ------------------------------------------------------------------ AJUDA */
const ajuda = { carregada: false, secoes: [], spy: null };
const semAcento = (t) => String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function carregarAjuda() {
  if (ajuda.carregada) return;
  const box = $("#aj-conteudo"), toc = $("#aj-toc");
  try {
    const r = await fetch("/api/ajuda", { credentials: "same-origin" });
    if (!r.ok) throw new Error("Não foi possível carregar o guia agora.");
    box.innerHTML = await r.text();
  } catch (e) {
    box.innerHTML = '<p class="muted">Não foi possível carregar o guia. Tente de novo em instantes.</p>';
    return toast(e.message, "erro");
  }
  ajuda.secoes = $$(".aj-sec", box).map((el) => ({ el, id: el.id, titulo: el.dataset.titulo, texto: semAcento(el.textContent) }));
  toc.innerHTML = "";
  ajuda.secoes.forEach((s, k) => {
    const a = document.createElement("a");
    a.href = "#" + s.id; a.textContent = s.titulo; a.dataset.alvo = s.id;
    a.addEventListener("click", (e) => { e.preventDefault(); s.el.scrollIntoView({ behavior: REDUZIR ? "auto" : "smooth", block: "start" }); });
    toc.appendChild(a); s.link = a;
  });
  // destaca no índice a seção que está na tela
  ajuda.spy?.disconnect();
  ajuda.spy = new IntersectionObserver((ents) => {
    ents.forEach((en) => { if (en.isIntersecting) ajuda.secoes.forEach((s) => s.link.classList.toggle("ativo", s.el === en.target)); });
  }, { root: $(".conteudo"), rootMargin: "-10% 0px -70% 0px" });
  ajuda.secoes.forEach((s) => ajuda.spy.observe(s.el));
  ajuda.carregada = true;
}

$("#aj-busca").addEventListener("input", (e) => {
  const q = semAcento(e.target.value.trim());
  let achou = 0;
  ajuda.secoes.forEach((s) => {
    const bate = !q || s.texto.includes(q);
    s.el.hidden = !bate; s.link.hidden = !bate;
    if (bate) achou++;
    $$("details", s.el).forEach((d) => { d.open = !!q && semAcento(d.textContent).includes(q); });
  });
  $("#aj-contagem").textContent = q ? (achou ? `${achou} seç${achou === 1 ? "ão" : "ões"}` : "Nada encontrado") : "";
});
