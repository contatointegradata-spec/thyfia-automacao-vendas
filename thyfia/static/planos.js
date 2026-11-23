/* =====================================================================
   ThyfIA — planos, passo a passo, automação, Instagram/Meta e administração
   (carregado depois de app.js: usa $, $$, api, toast, estado, irPara, confete…)
   ===================================================================== */
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const dia = (iso) => (iso ? new Date(iso.replace(" ", "T")).toLocaleDateString("pt-BR") : "—");
const NOME_PLANO = { plus: "Plus", pro: "Pro", premium: "Premium" };
// qual recurso libera cada tela
const TRAVA = { gerar: "gerar", integracoes: "gerar", historico: "gerar", cerebro: "gerar", automacao: "automacao", piloto: "automacao", publicacao: "agente" };
const PLANO_DO_RECURSO = { gerar: "plus", automacao: "pro", agente: "premium" };

const plano = { cat: null, espera: null, destaque: null, passos: [] };

/* ------------------------------------------------------------------ estado do plano no app */
function aplicarPlano(eu) {
  estado.admin = !!eu.admin;
  estado.plano = eu.plano || { plano: null, ativo: false, status: "sem_plano" };
  estado.liberados = eu.liberados || { gerar: false, automacao: false, agente: false };
  $$(".menu-item").forEach((m) => {
    const rec = TRAVA[m.dataset.view];
    const travado = !!rec && !estado.liberados[rec];
    m.classList.toggle("travado", travado);
    m.title = travado ? `Disponível no plano ${NOME_PLANO[PLANO_DO_RECURSO[rec]]}` : "";
  });
  $("#menu-admin").hidden = !estado.admin;
  const p = estado.plano;
  $("#pc-nome").textContent = estado.admin ? "Administrador" : p.ativo ? `Plano ${NOME_PLANO[p.plano]}` : "Sem plano";
  $("#pc-sub").textContent = estado.admin ? "Tudo liberado" : p.ativo
    ? (p.status === "cancelada" ? `Cancelado · vale até ${dia(p.valido_ate)}` : p.status === "atrasada" ? "Pagamento atrasado" : `Renova até ${dia(p.valido_ate)}`)
    : "Escolha um plano";
  $("#plano-chip").className = "plano-chip " + (estado.admin ? "admin" : p.ativo ? p.plano : "sem");
  $("#dot-plano").classList.toggle("on", !!p.ativo || estado.admin);
  requestAnimationFrame(moverIndicador);
}

// chamado por irPara(): devolve o recurso que falta, se a tela estiver bloqueada
function bloqueioDaView(view) {
  const rec = TRAVA[view];
  return rec && estado.liberados && !estado.liberados[rec] ? rec : null;
}

// qualquer API que responda 402 leva à tela de planos
function aoPlanoNecessario(msg) {
  toast(msg, "info");
  recarregarPerfil().then(() => { plano.destaque = null; irPara("plano"); });
}

async function recarregarPerfil() {
  try {
    const eu = await api("/api/eu");
    estado.usuario = { ...estado.usuario, ...eu };
    aplicarPlano(eu);
    aplicarPreferencias(eu.preferencias);
    return eu;
  } catch { return null; }
}

/* ------------------------------------------------------------------ MEU PLANO */
const STATUS_TXT = {
  ativa: ["Ativo", "conectado"], atrasada: ["Pagamento atrasado", "erro"], cancelada: ["Cancelado (vale até o fim do período)", "conectando"],
  reembolsada: ["Reembolsado", "erro"], sem_plano: ["Sem plano", ""], admin: ["Administrador", "conectado"],
};

async function carregarPlano(destaque) {
  if (destaque !== undefined) plano.destaque = destaque;
  const grade = $("#pl-grade");
  if (!plano.cat) grade.innerHTML = '<div class="skeleton" style="height:260px"></div>';
  try {
    const [cat, onb] = await Promise.all([api("/api/planos"), api("/api/onboarding")]);
    plano.cat = cat; plano.passos = onb.passos;
    renderPlano();
  } catch (e) { grade.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

function renderPlano() {
  const { planos, situacao: sit, suporte_whatsapp: zap, pagamento_configurado: pagOk } = plano.cat;
  const admin = !!sit.admin;
  // aviso do recurso bloqueado
  const av = $("#pl-aviso");
  if (plano.destaque) {
    av.hidden = false;
    av.innerHTML = `<b>${ic("lock")} ${esc(NOME_REC[plano.destaque])}</b> faz parte do plano <b>${NOME_PLANO[PLANO_DO_RECURSO[plano.destaque]]}</b> ou superior. Escolha abaixo para liberar.`;
  } else av.hidden = true;

  // cartão de status
  const [txt, cls] = STATUS_TXT[sit.status] || STATUS_TXT.sem_plano;
  $("#pl-status").innerHTML = admin
    ? `<div class="pl-st"><div class="pl-st-ic">${ic("sliders")}</div><div><strong>Administrador</strong><p class="muted">Você tem todos os recursos liberados, sem cobrança.</p></div><span class="status-pill conectado">Tudo liberado</span></div>`
    : sit.ativo
      ? `<div class="pl-st"><div class="pl-st-ic ${sit.plano}">${ic("star")}</div><div><strong>Plano ${NOME_PLANO[sit.plano]}</strong>
          <p class="muted">${sit.status === "cancelada" ? "Cancelado — você mantém o acesso até" : sit.status === "atrasada" ? "Regularize o pagamento para não perder o acesso. Acesso garantido até" : "Acesso garantido até"} <b>${dia(sit.valido_ate)}</b>${sit.origem === "manual" ? " · cortesia" : ""}</p></div>
          <span class="status-pill ${cls}">${txt}</span></div>`
      : `<div class="pl-st"><div class="pl-st-ic">${ic("star")}</div><div><strong>${sit.ultimo_plano ? "Seu plano não está mais ativo" : "Você ainda não tem um plano"}</strong>
          <p class="muted">${sit.status === "reembolsada" ? "O pagamento foi reembolsado." : sit.ultimo_plano ? "Renove para voltar a usar a plataforma." : "Escolha um dos planos abaixo para começar a gerar links."}</p></div>
          <span class="status-pill ${cls}">${txt}</span></div>`;
  $("#plano-sub").textContent = sit.ativo || admin ? "Veja o que está incluso, faça upgrade quando quiser e siga o passo a passo." :
    "Escolha o plano ideal para o seu momento. Você pode subir de plano quando quiser.";

  // cartões
  $("#pl-grade").innerHTML = planos.map((p) => {
    const alvo = plano.destaque && PLANO_DO_RECURSO[plano.destaque] === p.id;
    let acao;
    if (admin) acao = `<button class="btn btn-fantasma btn-grande" disabled>Incluído para você</button>`;
    else if (p.atual) acao = `<button class="btn btn-fantasma btn-grande" disabled>${ic("check")} Seu plano atual</button>`;
    else if (p.inclui_atual) acao = `<button class="btn btn-fantasma btn-grande" disabled>Já incluso no seu plano</button>`;
    else if (p.checkout) acao = `<button class="btn ${p.destaque || alvo ? "btn-primario" : "btn-fantasma"} btn-grande" data-comprar="${p.id}">${sit.ativo ? "Fazer upgrade" : "Assinar"} ${p.nome}</button>`;
    else acao = `<button class="btn btn-fantasma btn-grande" data-suporte="1">Falar com o suporte</button>`;
    return `<article class="pl-card ${p.id} ${p.destaque ? "destaque" : ""} ${p.atual ? "atual" : ""} ${alvo ? "alvo" : ""}" style="--i:${p.nivel}">
      ${p.destaque ? '<span class="pl-faixa">Mais escolhido</span>' : ""}
      <h3>${p.nome}</h3>
      <p class="pl-resumo">${esc(p.resumo)}</p>
      <div class="pl-preco"><b>${brl(p.preco)}</b><small>/mês</small></div>
      <ul class="pl-lista">
        ${p.recursos.map((r) => `<li class="sim">${esc(r)}</li>`).join("")}
        ${p.nao_inclui.map((r) => `<li class="nao">${esc(r)}</li>`).join("")}
      </ul>
      ${acao}
    </article>`;
  }).join("");
  $("#pl-nota").innerHTML = pagOk || admin
    ? `${ic("bulb")} Na hora de pagar, use o <b>mesmo e-mail</b> da sua conta: <b>${esc(estado.usuario.email)}</b>. O plano libera sozinho assim que o pagamento é confirmado.`
    : `${ic("alert")} O pagamento ainda está sendo configurado.${zap ? ` Fale com o suporte: <a href="https://wa.me/${esc(zap)}" target="_blank" rel="noopener">WhatsApp</a>.` : ""}`;

  renderTabelaPlanos(planos);
  renderPassos();
}

const NOME_REC = { gerar: "O gerador de links", automacao: "A automação", agente: "O Agente de Publicação" };

function renderTabelaPlanos(planos) {
  const linhas = [
    ["Gerador de links + planilha", [1, 1, 1]], ["Histórico e Cérebro", [1, 1, 1]], ["Central de Ajuda", [1, 1, 1]],
    ["Envio automático para a automação", [0, 1, 1]], ["Divulgação no seu WhatsApp e Telegram", [0, 1, 1]],
    ["Agente de Publicação (Instagram)", [0, 0, 1]], ["Passo a passo para ligar o Instagram/Meta", [0, 0, 1]],
  ];
  $("#pl-tabela").innerHTML = `<thead><tr><th></th>${planos.map((p) => `<th class="${p.id}">${p.nome}<small>${brl(p.preco)}/mês</small></th>`).join("")}</tr></thead>
    <tbody>${linhas.map(([n, v]) => `<tr><td>${n}</td>${v.map((x) => `<td>${x ? '<span class="ck">' + ic("check") + '</span>' : '<span class="tr">—</span>'}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

/* ---- passo a passo do plano contratado ---- */
const PASSOS_INFO = {
  ml: ["Conecte o Mercado Livre", "A ThyfIA usa o login que você já tem no navegador, pela extensão <b>ThyfIA Conector</b>. Sua senha nunca passa por aqui. Em <b>Integrações</b>, clique em “Conectar Mercado Livre” e siga as instruções na tela (é uma vez só)."],
  gerar: ["Gere seus primeiros links", "Em <b>Gerar links</b>, digite um produto (ex.: “capinha iphone”), escolha quantos quer e clique em <b>Gerar links</b>. Ao terminar, baixe a planilha."],
  whatsapp: ["Informe seu WhatsApp", "Em <b>Automação</b>, salve o número que vai receber as ofertas prontas. Use “Enviar mensagem de teste” para conferir."],
  telegram: ["Ligue seu canal do Telegram", "Opcional. Adicione o bot da ThyfIA como administrador do seu canal e salve o @ em <b>Automação</b>."],
  envio: ["Envie links para a automação", "Depois de gerar, clique em <b>Enviar para automação</b> (ou ligue o envio automático em Automação). A partir daí as ofertas são divulgadas sozinhas."],
  meta: ["Conecte Instagram/Meta", "Em <b>Agente de Publicação</b>, siga o passo a passo guiado: criar o app na Meta, gerar o token e colar na ThyfIA."],
  post: ["Agende o primeiro post", "Clique em <b>Sugerir melhor item</b>, revise a prévia e agende a publicação no Instagram."],
};

function renderPassos() {
  const box = $("#pl-passos-box");
  const sit = plano.cat.situacao;
  if (!plano.passos.length) { box.hidden = true; atualizarBannerPassos(); return; }
  box.hidden = false;
  const obrig = plano.passos.filter((p) => !p.opcional);
  const feitos = obrig.filter((p) => p.feito).length;
  const proximo = plano.passos.find((p) => !p.feito && !p.opcional);
  $("#pl-passos-pill").textContent = feitos === obrig.length ? "Tudo pronto" : `${feitos} de ${obrig.length} concluídos`;
  $("#pl-passos-pill").className = "status-pill " + (feitos === obrig.length ? "conectado" : "conectando");
  $("#pl-passos-barra").style.width = Math.round((feitos / Math.max(obrig.length, 1)) * 100) + "%";
  $("#pl-passos").innerHTML = plano.passos.map((p, i) => {
    const [t, d] = PASSOS_INFO[p.id];
    const eh = proximo && proximo.id === p.id;
    return `<li class="pl-passo ${p.feito ? "feito" : ""} ${eh ? "proximo" : ""}">
      <span class="pl-bola">${p.feito ? ic("check") : i + 1}</span>
      <div class="pl-passo-corpo"><strong>${t}${p.opcional ? ' <em>opcional</em>' : ""}${eh ? ' <span class="tag-prox">próximo passo</span>' : ""}</strong><p>${d}</p></div>
      <button class="btn ${eh ? "btn-primario" : "btn-fantasma"} btn-mini" data-ir="${p.ir}">${p.feito ? "Abrir" : "Fazer agora"}</button>
    </li>`;
  }).join("");
  atualizarBannerPassos();
}

// faixinha de progresso na tela "Gerar links"
function atualizarBannerPassos() {
  const b = $("#onb-banner");
  const obrig = plano.passos.filter((p) => !p.opcional);
  const feitos = obrig.filter((p) => p.feito).length;
  if (!obrig.length || feitos === obrig.length) { b.hidden = true; return; }
  const prox = obrig.find((p) => !p.feito);
  b.hidden = false;
  $("#onb-num").textContent = `${feitos}/${obrig.length}`;
  $("#onb-anel").style.strokeDashoffset = String(94.2 * (1 - feitos / obrig.length));
  $("#onb-titulo").textContent = "Complete a configuração do seu plano";
  $("#onb-sub").textContent = `Próximo: ${PASSOS_INFO[prox.id][0]}`;
}

async function atualizarPassos() {
  if (!estado.plano?.ativo && !estado.admin) { plano.passos = []; atualizarBannerPassos(); return; }
  try { plano.passos = (await api("/api/onboarding")).passos; atualizarBannerPassos(); } catch { /* sem banner */ }
}

/* ---- compra: abre o checkout e fica esperando o pagamento ---- */
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-comprar]");
  if (c) {
    const p = plano.cat.planos.find((x) => x.id === c.dataset.comprar);
    window.open(p.checkout, "_blank", "noopener");
    toast("Checkout aberto em outra aba. Esta tela libera sozinha após o pagamento.", "info");
    aguardarPagamento();
  }
  if (e.target.closest("[data-suporte]")) {
    const z = plano.cat?.suporte_whatsapp;
    if (z) window.open(`https://wa.me/${z}`, "_blank", "noopener"); else toast("Suporte ainda não configurado. Tente mais tarde.", "info");
  }
});

function aguardarPagamento() {
  clearInterval(plano.espera);
  $("#pl-espera").hidden = false;
  const base = estado.plano?.plano;
  let n = 0;
  plano.espera = setInterval(async () => {
    n++;
    if (await conferirPagamento(base)) return;
    if (n > 100) { clearInterval(plano.espera); $("#pl-espera").hidden = true; }  // ~10 min
  }, 6000);
}

async function conferirPagamento(base) {
  const eu = await recarregarPerfil();
  if (!eu) return false;
  if (eu.plano.ativo && eu.plano.plano !== base) {
    clearInterval(plano.espera); $("#pl-espera").hidden = true;
    confete(220);
    toast(`Plano ${NOME_PLANO[eu.plano.plano]} liberado!`, "ok");
    plano.destaque = null; await carregarPlano();
    $("#pl-passos-box").scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  }
  return false;
}
$("#btn-pl-verificar").addEventListener("click", async (ev) => {
  const b = ev.currentTarget; carregando(b, true);
  const ok = await conferirPagamento(estado.plano?.plano);
  carregando(b, false);
  if (!ok) toast("Ainda não recebemos a confirmação. Aguarde um instante e tente de novo.", "info");
});

/* ------------------------------------------------------------------ AUTOMAÇÃO (Pro) */
const au = { cfg: null };

async function abrirAutomacao() {
  try { au.cfg = await api("/api/conta/config"); } catch (e) { return toast(e.message, "erro"); }
  renderAutomacao();
}

function pill(ok, sim, nao) { return `<span class="status-pill ${ok ? "conectado" : ""}">${ok ? sim : nao}</span>`; }

function renderAutomacao() {
  const c = au.cfg;
  const pronta = !!(c.whatsapp_numero || c.telegram_canal);
  $("#au-resumo").innerHTML = `
    <div class="au-chip"><span>Plataforma</span>${pill(c.plataforma_ok, "online", "indisponível")}</div>
    <div class="au-chip"><span>WhatsApp</span>${pill(!!c.whatsapp_numero, esc(c.whatsapp_formatado), "não informado")}</div>
    <div class="au-chip"><span>Telegram</span>${pill(!!c.telegram_canal, esc(c.telegram_canal), "não ligado")}</div>
    <div class="au-chip"><span>Envio automático</span>${pill($("#chk-auto-envio").checked, "ligado", "desligado")}</div>`;
  if (!c.plataforma_ok) msgAu("A automação da plataforma ainda não foi habilitada. Fale com o suporte para ativar.", "erro");
  else msgAu("", "");
  const inW = $("#au-whats");
  if (document.activeElement !== inW) inW.value = c.whatsapp_formatado || "";
  prevWhats();
  const inT = $("#au-tg");
  if (document.activeElement !== inT) inT.value = c.telegram_canal || "";
  $("#au-bot").textContent = c.bot_telegram ? c.bot_telegram : "da ThyfIA (peça o @ ao suporte)";
  $("#btn-au-teste-whats").hidden = !(c.whatsapp_numero && c.teste_whatsapp_disponivel);
  $("#btn-au-teste-tg").hidden = !(c.telegram_canal && c.teste_telegram_disponivel);
  $("#au-p1").classList.toggle("feito", !!c.whatsapp_numero);
  $("#au-p2").classList.toggle("feito", !!c.telegram_canal);
  $("#au-p3").classList.toggle("feito", $("#chk-auto-envio").checked);
  $("#au-p1 .au-num").innerHTML = c.whatsapp_numero ? ic("check") : "1";
  $("#au-p2 .au-num").innerHTML = c.telegram_canal ? ic("check") : "2";
  $("#au-p3 .au-num").innerHTML = $("#chk-auto-envio").checked ? ic("check") : "3";
}
function msgAu(t, tipo) { const m = $("#au-msg"); m.textContent = t; m.className = "envio-msg " + (tipo || ""); }

function prevWhats() {
  const v = $("#au-whats").value.replace(/\D/g, "");
  const p = $("#au-whats-prev");
  if (!v) { p.textContent = "Ex.: (34) 99999-9999 — o código do país (+55) é colocado automaticamente."; return; }
  const n = v.length <= 11 ? "55" + v : v;
  p.textContent = n.length >= 12 && n.length <= 13 ? `Será enviado para +${n.slice(0, 2)} (${n.slice(2, 4)}) ${n.slice(4, -4)}-${n.slice(-4)}` : "Digite DDD + número.";
}
$("#au-whats").addEventListener("input", prevWhats);

$("#form-whats").addEventListener("submit", async (e) => {
  e.preventDefault();
  const b = $("#btn-au-salvar"); carregando(b, true);
  try {
    au.cfg = { ...au.cfg, ...(await api("/api/conta/canais", { method: "PUT", body: { whatsapp: $("#au-whats").value, telegram: au.cfg.telegram_canal || "" } })) };
    toast("WhatsApp salvo", "ok"); renderAutomacao(); atualizarPassos(); recarregarPerfil();
  } catch (err) { toast(err.message, "erro"); } finally { carregando(b, false); }
});
$("#form-tg").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!au.cfg.whatsapp_numero) return toast("Salve primeiro o seu WhatsApp (passo 1).", "info");
  const b = $("#btn-au-salvar-tg"); carregando(b, true);
  try {
    au.cfg = { ...au.cfg, ...(await api("/api/conta/canais", { method: "PUT", body: { whatsapp: au.cfg.whatsapp_numero, telegram: $("#au-tg").value } })) };
    toast(au.cfg.telegram_canal ? "Canal salvo" : "Telegram desligado.", "ok"); renderAutomacao(); atualizarPassos();
  } catch (err) { toast(err.message, "erro"); } finally { carregando(b, false); }
});
async function testar(btn, rota, ok) {
  carregando(btn, true);
  try { await api(rota, { body: {} }); toast(ok, "ok"); } catch (err) { toast(err.message, "erro"); } finally { carregando(btn, false); }
}
$("#btn-au-teste-whats").addEventListener("click", (e) => testar(e.currentTarget, "/api/conta/canais/testar-whatsapp", "Mensagem de teste enviada. Confira seu WhatsApp!"));
$("#btn-au-teste-tg").addEventListener("click", (e) => testar(e.currentTarget, "/api/conta/canais/testar-telegram", "Teste enviado ao canal. Confira o Telegram!"));

/* ------------------------------------------------------------------ INSTAGRAM / META (Premium) */
const IG_PASSOS = [
  ["Tenha uma conta profissional no Instagram", `<p>O Instagram só deixa programas publicarem em contas <b>Comercial</b> ou <b>Criador de conteúdo</b> (é grátis).</p>
    <ol><li>No app do Instagram: <b>Configurações e privacidade</b> → <b>Tipo de conta e ferramentas</b>.</li><li>Toque em <b>Mudar para conta profissional</b> e escolha Comercial ou Criador.</li></ol>`],
  ["Crie sua conta de desenvolvedor na Meta", `<p>É o que permite ter um “app” seu — gratuito.</p>
    <ol><li>Acesse <a href="https://developers.facebook.com" target="_blank" rel="noopener">developers.facebook.com</a> e entre com o seu Facebook.</li><li>Clique em <b>Começar</b>, aceite os termos e confirme seu celular/e-mail se pedir.</li></ol>`],
  ["Crie o seu app", `<ol><li>Em <a href="https://developers.facebook.com/apps" target="_blank" rel="noopener">Meus apps</a>, clique em <b>Criar app</b>.</li>
    <li>Dê um nome (ex.: “ThyfIA — sua loja”) e informe seu e-mail.</li>
    <li>No caso de uso, escolha <b>Gerenciar mensagens e conteúdo no Instagram</b> (Instagram API) e, se perguntar o tipo, <b>Empresa</b>.</li>
    <li>Conclua a criação. O app fica em <b>modo de desenvolvimento</b> — é exatamente o que você precisa, <u>não precisa enviar para análise da Meta</u>.</li></ol>`],
  ["Adicione o seu Instagram como testador", `<p>Como o app é só seu, você se coloca como “Testador do Instagram”.</p>
    <ol><li>No painel do app: <b>Funções do app</b> → <b>Funções</b> → <b>Adicionar pessoas</b> → <b>Testador do Instagram</b>, e digite o @ da sua conta.</li>
    <li>Abra o <b>Instagram</b> → <b>Configurações</b> → <b>Apps e sites</b> → <b>Convites de testador</b> e toque em <b>Aceitar</b>.</li>
    <li>Volte ao painel: o status deve mudar de “Pendente” para <b>aceito</b>.</li></ol>`],
  ["Gere o token de acesso", `<ol><li>No painel do app, abra <b>Instagram</b> → <b>Configuração da API com login do Instagram</b>.</li>
    <li>Na seção <b>Gerar tokens de acesso</b>, clique em <b>Adicionar conta</b> (se precisar), escolha a sua e clique em <b>Gerar token</b>.</li>
    <li>Entre no Instagram se pedir e autorize, <b>marcando a permissão de publicar conteúdo</b>.</li>
    <li><b>Copie o token</b> e também o <b>ID da conta</b> (um número grande, na mesma tela, perto do @).</li></ol>
    <p class="mini">O token vale cerca de <b>60 dias</b>. Quando expirar, é só gerar outro e colar aqui — a ThyfIA te avisa.</p>`],
];

function lerPassosIg() { try { return JSON.parse(localStorage.getItem(`thyfia_ig_${estado.usuario.id}`) || "[]"); } catch { return []; } }
function gravarPassosIg(v) { try { localStorage.setItem(`thyfia_ig_${estado.usuario.id}`, JSON.stringify(v)); } catch { /* sem armazenamento */ } }

function renderWizardIg() {
  const feitos = lerPassosIg();
  $("#wz-lista").innerHTML = IG_PASSOS.map(([t, h], i) => `<details class="wz-passo ${feitos.includes(i) ? "feito" : ""}" ${i === (feitos.length < IG_PASSOS.length ? Math.min(...IG_PASSOS.map((_, k) => k).filter((k) => !feitos.includes(k))) : -1) ? "open" : ""}>
    <summary><span class="wz-bola">${feitos.includes(i) ? ic("check") : i + 1}</span><span class="wz-titulo">${t}</span></summary>
    <div class="wz-corpo">${h}<label class="wz-ok"><input type="checkbox" data-wz="${i}" ${feitos.includes(i) ? "checked" : ""}> Concluí este passo</label></div></details>`).join("");
  $("#wz-barra").style.width = Math.round((feitos.length / IG_PASSOS.length) * 100) + "%";
  $("#wz-contagem").textContent = `${feitos.length} de ${IG_PASSOS.length} passos marcados`;
}
$("#wz-lista").addEventListener("change", (e) => {
  const c = e.target.closest("[data-wz]"); if (!c) return;
  const i = Number(c.dataset.wz); let f = lerPassosIg();
  f = c.checked ? [...new Set([...f, i])] : f.filter((x) => x !== i);
  gravarPassosIg(f); renderWizardIg();
});
$("#ig-olho").addEventListener("click", () => { const i = $("#ig-in-token"); i.type = i.type === "password" ? "text" : "password"; });

function mostrarWizardIg(sim) {
  $("#ig-wizard").hidden = !sim;
  if (sim) renderWizardIg();
}
$("#btn-ig-conectar-abrir").addEventListener("click", () => { mostrarWizardIg(true); $("#ig-wizard").scrollIntoView({ behavior: "smooth", block: "start" }); });
$("#btn-ig-trocar").addEventListener("click", () => { mostrarWizardIg(true); $("#btn-wz-fechar").hidden = false; $("#ig-wizard").scrollIntoView({ behavior: "smooth", block: "start" }); });
$("#btn-wz-fechar").addEventListener("click", () => mostrarWizardIg(false));

function msgIg(t, tipo) { const m = $("#ig-msg"); m.textContent = t; m.className = "envio-msg " + (tipo || ""); }

$("#form-ig").addEventListener("submit", async (e) => {
  e.preventDefault();
  const b = $("#btn-ig-conectar"); carregando(b, true); msgIg("Conferindo com a Meta…", "");
  try {
    const r = await api("/api/conta/instagram", { method: "PUT", body: { user_id: $("#ig-in-id").value, token: $("#ig-in-token").value } });
    $("#ig-in-token").value = ""; $("#ig-in-id").value = "";
    msgIg(`Conectado a @${r.username || "sua conta"}`, "ok");
    confete(160); toast("Instagram conectado!", "ok");
    await espera(900);
    mostrarWizardIg(false); abrirPublicacao(); atualizarPassos();
  } catch (err) { msgIg(err.message, "erro"); } finally { carregando(b, false); }
});
$("#btn-ig-remover").addEventListener("click", async () => {
  if (!confirm("Desconectar o Instagram? Os posts agendados deixam de sair até você conectar de novo.")) return;
  try { await api("/api/conta/instagram", { method: "DELETE" }); toast("Instagram desconectado.", "ok"); abrirPublicacao(); } catch (err) { toast(err.message, "erro"); }
});

// chamado por statusInstagram() para decidir o que mostrar
function ajustarTelaInstagram(s) {
  const semConta = !s.configurado;
  $("#btn-ig-conectar-abrir").hidden = !semConta;
  $("#btn-ig-trocar").hidden = semConta;
  $("#btn-ig-remover").hidden = semConta;
  $("#btn-ig-atualizar").hidden = semConta;
  ["pub-controles", "pub-info", "pub-candidatos", "pub-aviso"].forEach((id) => { const el = $("#" + id); if (el) el.hidden = semConta; });
  if (semConta) { mostrarWizardIg(true); $("#btn-wz-fechar").hidden = true; }
  else if (!$("#ig-wizard").hidden && $("#btn-wz-fechar").hidden) mostrarWizardIg(false);
}

/* ------------------------------------------------------------------ ADMINISTRAÇÃO */
const ad = { aba: "resumo" };
const ROT_PLANO_ADM = { null: "—", plus: "Plus", pro: "Pro", premium: "Premium" };

function abrirAdmin() { trocarAbaAdmin(ad.aba); }
function trocarAbaAdmin(aba) {
  ad.aba = aba;
  $$(".ad-aba").forEach((b) => b.classList.toggle("ativo", b.dataset.aba === aba));
  $$(".ad-painel").forEach((p) => (p.hidden = p.id !== "ad-" + aba));
  ({ resumo: admResumo, usuarios: admUsuarios, pagamentos: admPagamentos, config: admConfig })[aba]();
}
$("#ad-abas").addEventListener("click", (e) => { const b = e.target.closest(".ad-aba"); if (b) trocarAbaAdmin(b.dataset.aba); });

async function admResumo() {
  const box = $("#ad-resumo");
  try {
    const [r, cfg] = await Promise.all([api("/api/admin/resumo"), api("/api/admin/config")]);
    const c = Object.fromEntries(cfg.campos.map((x) => [x.chave, x]));
    const checks = [
      ["Token do webhook da Kiwify", c.kiwify_token.definido], ["Plano Plus na Kiwify", c.kiwify_produto_plus.definido],
      ["Plano Pro na Kiwify", c.kiwify_produto_pro.definido], ["Plano Premium na Kiwify", c.kiwify_produto_premium.definido],
      ["Links de checkout dos 3 planos", c.kiwify_link_plus.definido && c.kiwify_link_pro.definido && c.kiwify_link_premium.definido],
      ["Automação (n8n) habilitada", cfg.n8n_ok], ["Teste de WhatsApp (Evolution)", c.evolution_url.definido && c.evolution_apikey.definido && c.evolution_instancia.definido],
      ["Teste de Telegram (bot)", c.telegram_bot_token.definido && c.telegram_bot.definido],
      ["E-mail de boas-vindas (Gmail)", c.email_remetente.definido && c.email_senha_app.definido],
    ];
    const pronto = checks.filter((x) => x[1]).length;
    box.innerHTML = `<div class="stats ad-stats">
        <div class="stat"><small>Clientes</small><strong>${r.usuarios}</strong></div>
        <div class="stat"><small>Assinantes Plus / Pro / Premium</small><strong>${r.assinantes.plus} / ${r.assinantes.pro} / ${r.assinantes.premium}</strong></div>
        <div class="stat"><small>Receita mensal (estimada)</small><strong>${brl(r.receita_mensal)}</strong></div>
        <div class="stat"><small>Gerações nos últimos 30 dias</small><strong>${r.jobs_30d}</strong></div></div>
      <section class="painel"><div class="pl-passos-topo"><h3>Checklist para vender</h3><span class="status-pill ${pronto === checks.length ? "conectado" : "conectando"}">${pronto} de ${checks.length}</span></div>
        <ul class="ad-check">${checks.map(([n, ok]) => `<li class="${ok ? "ok" : ""}"><span>${ok ? ic("check") : ""}</span>${n}</li>`).join("")}</ul>
        <button class="btn btn-fantasma" data-aba-ir="config">Abrir configurações</button></section>`;
  } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

let admTimer;
async function admUsuarios(busca = "") {
  const box = $("#ad-usuarios");
  if (!box.dataset.pronto) {
    box.dataset.pronto = "1";
    box.innerHTML = `<div class="ad-barra"><input id="ad-busca" type="search" placeholder="Buscar por nome ou e-mail…" autocomplete="off">
      <button class="btn btn-primario btn-mini" id="btn-ad-conceder">＋ Liberar plano</button></div><div id="ad-lista" class="ad-lista"></div>`;
    $("#ad-busca").addEventListener("input", (e) => { clearTimeout(admTimer); admTimer = setTimeout(() => admUsuarios(e.target.value), 250); });
    $("#btn-ad-conceder").addEventListener("click", () => abrirConcessao(""));
  }
  try {
    const us = await api("/api/admin/usuarios?busca=" + encodeURIComponent(busca));
    $("#ad-lista").innerHTML = us.length ? us.map((u) => `<article class="ad-user ${u.ativo ? "" : "inativo"}">
      <div class="ad-user-id"><div class="avatar">${esc(u.nome[0] || "?").toUpperCase()}</div>
        <div><strong>${esc(u.nome)} ${u.papel === "admin" ? '<span class="selo-plano">admin</span>' : ""}</strong><small>${esc(u.email)}</small></div></div>
      <div class="ad-user-info"><span class="status-pill ${u.plano ? "conectado" : ""}">${u.plano ? ROT_PLANO_ADM[u.plano] : "Sem plano"}</span>
        <small>${u.plano ? `${esc(u.status)} · até ${dia(u.valido_ate)}${u.origem === "manual" ? " · manual" : ""}` : esc(u.status === "sem_plano" ? "nunca assinou" : u.status)}</small>
        <small>ML: ${esc(u.ml)} · ${u.jobs} geração(ões) · desde ${dia(u.criado)}</small></div>
      <div class="ad-user-acoes">
        <button class="btn btn-fantasma btn-mini" data-conceder="${esc(u.email)}">Liberar plano</button>
        ${u.plano && u.papel !== "admin" ? `<button class="btn btn-fantasma btn-mini" data-revogar="${esc(u.email)}|${u.plano}">Revogar ${ROT_PLANO_ADM[u.plano]}</button>` : ""}
        ${u.id !== estado.usuario.id ? `<button class="btn btn-fantasma btn-mini" data-ativo="${u.id}|${u.ativo ? 0 : 1}">${u.ativo ? "Desativar" : "Reativar"}</button>
        <button class="btn btn-fantasma btn-mini" data-admin="${u.id}|${u.papel === "admin" ? 0 : 1}">${u.papel === "admin" ? "Tirar admin" : "Tornar admin"}</button>` : ""}
      </div></article>`).join("") : '<p class="muted">Nenhum cliente encontrado.</p>';
  } catch (e) { $("#ad-lista").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

function abrirConcessao(email) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal ad-modal"><h2>Liberar plano manualmente</h2>
    <p class="muted">Use para cortesias, testes ou para corrigir um pagamento feito com outro e-mail.</p>
    <label class="pub-campo"><span>E-mail do cliente</span><input type="email" id="cc-email" value="${esc(email)}" placeholder="cliente@email.com"></label>
    <label class="pub-campo"><span>Plano</span><select id="cc-plano"><option value="plus">Plus</option><option value="pro">Pro</option><option value="premium">Premium</option></select></label>
    <label class="pub-campo"><span>Dias de acesso</span><input type="number" id="cc-dias" min="1" max="3650" value="31"></label>
    <label class="pub-campo"><span>Observação (opcional)</span><input type="text" id="cc-obs" maxlength="200" placeholder="ex.: cortesia parceiro"></label>
    <p class="envio-msg" id="cc-msg"></p>
    <div class="au-acoes"><button class="btn btn-primario" id="cc-ok"><span class="btn-txt">Liberar</span><span class="spinner"></span></button><button class="btn btn-fantasma" id="cc-cancelar">Cancelar</button></div></div>`;
  document.body.appendChild(ov);
  const fechar = () => ov.remove();
  $("#cc-cancelar", ov).addEventListener("click", fechar);
  ov.addEventListener("click", (e) => { if (e.target === ov) fechar(); });
  $("#cc-ok", ov).addEventListener("click", async (e) => {
    const b = e.currentTarget; carregando(b, true);
    try {
      await api("/api/admin/assinaturas", { body: { email: $("#cc-email", ov).value, plano: $("#cc-plano", ov).value, dias: Number($("#cc-dias", ov).value), observacao: $("#cc-obs", ov).value } });
      toast("Plano liberado", "ok"); fechar(); admUsuarios($("#ad-busca")?.value || "");
    } catch (err) { $("#cc-msg", ov).textContent = err.message; $("#cc-msg", ov).className = "envio-msg erro"; } finally { carregando(b, false); }
  });
  $("#cc-email", ov).focus();
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("#view-admin :is([data-conceder],[data-revogar],[data-ativo],[data-admin],[data-aba-ir])");
  if (!t) return;
  try {
    if (t.dataset.conceder !== undefined) return abrirConcessao(t.dataset.conceder);
    if (t.dataset.abaIr) return trocarAbaAdmin(t.dataset.abaIr);
    if (t.dataset.revogar) {
      const [email, p] = t.dataset.revogar.split("|");
      if (!confirm(`Revogar o plano ${NOME_PLANO[p]} de ${email}? O acesso é cortado agora.`)) return;
      await api(`/api/admin/assinaturas?email=${encodeURIComponent(email)}&plano=${p}`, { method: "DELETE" });
    }
    if (t.dataset.ativo) { const [id, v] = t.dataset.ativo.split("|"); await api(`/api/admin/usuarios/${id}`, { method: "PUT", body: { ativo: v === "1" } }); }
    if (t.dataset.admin) {
      const [id, v] = t.dataset.admin.split("|");
      if (!confirm(v === "1" ? "Dar acesso de administrador a este usuário?" : "Remover o acesso de administrador?")) return;
      await api(`/api/admin/usuarios/${id}`, { method: "PUT", body: { admin: v === "1" } });
    }
    toast("Feito", "ok"); admUsuarios($("#ad-busca")?.value || "");
  } catch (err) { toast(err.message, "erro"); }
});

async function admPagamentos() {
  const box = $("#ad-pagamentos");
  try {
    const [cfg, ev] = await Promise.all([api("/api/admin/config"), api("/api/admin/eventos")]);
    const RES = { aplicado: "conectado", ignorado: "", recusado: "erro", erro: "erro" };
    box.innerHTML = `<section class="painel"><h3>Webhook da Kiwify</h3>
      <ol class="passos au-lista">
        <li>Na Kiwify: <b>Apps → Webhooks → Criar webhook</b>.</li>
        <li>Em <b>URL</b>, cole o endereço abaixo.</li>
        <li>Em <b>Token</b>, use o mesmo valor salvo em <b>Configurações → Token do webhook da Kiwify</b>.</li>
        <li>Em <b>Produtos</b>, escolha os 3 produtos de plano. Em <b>Eventos</b>, marque: compra aprovada, compra reembolsada, chargeback, assinatura cancelada, assinatura atrasada e assinatura renovada.</li></ol>
      <div class="ad-copiar"><code id="ad-wh">${esc(cfg.webhook_url)}</code><button class="btn btn-fantasma btn-mini" id="btn-ad-copiar">Copiar</button></div>
      <p class="mini">Cada chamada vem assinada pela Kiwify; chamadas sem assinatura válida são recusadas e aparecem no registro abaixo.</p></section>
      <section class="painel"><h3>Testar o vínculo Kiwify → plano</h3>
        <p class="muted">Simula uma compra aprovada pelo mesmo caminho do webhook real (cria uma assinatura de verdade para o e-mail informado).</p>
        <div class="ad-sim"><input id="sim-email" type="email" placeholder="e-mail do cliente"><input id="sim-prod" type="text" placeholder="nome do plano na Kiwify (ex.: ThyfIA Plus)">
        <button class="btn btn-fantasma" id="btn-sim">Simular compra</button></div><p class="envio-msg" id="sim-msg"></p></section>
      <section class="painel"><div class="pl-passos-topo"><h3>Últimos eventos</h3><button class="btn btn-fantasma btn-mini" id="btn-ev-atualizar">↻ Atualizar</button></div>
        <div class="ad-eventos">${ev.length ? ev.map((x) => `<div class="ad-ev"><span class="status-pill ${RES[x.resultado] || ""}">${esc(x.resultado)}</span>
          <div><strong>${esc(x.evento || "—")}</strong> · ${esc(x.email || "sem e-mail")}${x.plano ? ` · ${NOME_PLANO[x.plano]}` : ""}<small>${esc(x.detalhe || "")} — ${esc(x.recebido_em)}</small></div></div>`).join("") : '<p class="muted">Nenhum evento recebido ainda.</p>'}</div></section>`;
    $("#btn-ad-copiar").addEventListener("click", () => navigator.clipboard?.writeText(cfg.webhook_url).then(() => toast("Endereço copiado", "ok")));
    $("#btn-ev-atualizar").addEventListener("click", admPagamentos);
    $("#btn-sim").addEventListener("click", async (e) => {
      const b = e.currentTarget; carregando(b, true); const m = $("#sim-msg");
      try {
        const r = await api("/api/admin/kiwify/simular", { body: { email: $("#sim-email").value, produto: $("#sim-prod").value } });
        m.textContent = r.resultado === "aplicado" ? `Funcionou: plano ${NOME_PLANO[r.plano]} liberado para ${$("#sim-email").value}.` : `Não aplicou: ${r.detalhe || r.resultado}. Confira o vínculo em Configurações.`;
        m.className = "envio-msg " + (r.resultado === "aplicado" ? "ok" : "erro");
        admPagamentos();
      } catch (err) { m.textContent = err.message; m.className = "envio-msg erro"; } finally { carregando(b, false); }
    });
  } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

const GRUPOS_CFG = [
  ["Pagamento (Kiwify)", ["kiwify_token", "kiwify_produto_plus", "kiwify_produto_pro", "kiwify_produto_premium", "kiwify_link_plus", "kiwify_link_pro", "kiwify_link_premium"],
    "Em “Plano … na Kiwify”, informe o nome exato do plano de assinatura (ex.: ThyfIA Plus) ou o ID. Serve tanto para 1 produto com 3 planos quanto para 1 produto por plano; pode listar vários separados por vírgula. Os links são os de checkout de cada plano."],
  ["Automação (n8n)", ["n8n_webhook_url", "n8n_api_key"], "Webhook do fluxo “Receber links da ThyfIA”. Um só para todos os clientes: cada envio leva os dados do cliente."],
  ["WhatsApp (Evolution API)", ["evolution_url", "evolution_apikey", "evolution_instancia"], "Só para o botão “Enviar mensagem de teste” do cliente. A instância é a que envia as ofertas."],
  ["Telegram", ["telegram_bot", "telegram_bot_token"], "O @ do bot é mostrado ao cliente no passo a passo; o token só serve para o teste de envio ao canal."],
  ["E-mail de boas-vindas (Gmail)", ["email_remetente", "email_senha_app"], "Enviado uma vez a quem cria conta. Use o Gmail da ThyfIA e uma senha de app (Conta Google > Segurança > Verificação em duas etapas > Senhas de app). Sem os dois campos, nada é enviado."],
  ["Geral", ["url_publica", "suporte_whatsapp"], "Endereço público (ex.: https://thyfia.integradata.app.br) e o WhatsApp de suporte, só dígitos com DDI."],
];

async function admConfig() {
  const box = $("#ad-config");
  try {
    const cfg = await api("/api/admin/config");
    const c = Object.fromEntries(cfg.campos.map((x) => [x.chave, x]));
    box.innerHTML = `<form id="form-cfg" autocomplete="off">${GRUPOS_CFG.map(([t, ks, d]) => `<section class="painel ad-grupo"><h3>${t}</h3><p class="muted">${d}</p>
      ${ks.map((k) => `<label class="pub-campo"><span>${esc(c[k].rotulo)} ${c[k].definido ? '<em class="ok-mini">● definido</em>' : '<em class="nok-mini">○ vazio</em>'}</span>
        <input type="${c[k].segredo ? "password" : "text"}" data-cfg="${k}" value="${esc(c[k].valor)}" placeholder="${c[k].segredo ? (c[k].definido ? c[k].mascarado + " (deixe vazio para manter)" : "cole aqui") : ""}" autocomplete="off" spellcheck="false"></label>`).join("")}${ks.includes("email_remetente") ? `<button class="btn btn-fantasma" type="button" id="btn-teste-email"><span class="btn-txt">Enviar e-mail de teste para mim</span><span class="spinner"></span></button><p class="envio-msg" id="msg-teste-email"></p>` : ""}</section>`).join("")}
      <div class="au-acoes"><button class="btn btn-primario btn-grande" type="submit" id="btn-cfg"><span class="btn-txt">Salvar configurações</span><span class="spinner"></span></button></div></form>`;
    $("#btn-teste-email").addEventListener("click", async (e) => {
      const b = e.currentTarget, m = $("#msg-teste-email"); carregando(b, true); m.textContent = "";
      try { const r = await api("/api/admin/config/testar-email", { method: "POST", body: {} }); m.textContent = "Enviado para " + r.para + ". Confira a caixa de entrada (e o spam)."; m.className = "envio-msg ok"; }
      catch (err) { m.textContent = err.message; m.className = "envio-msg erro"; } finally { carregando(b, false); }
    });
    $("#form-cfg").addEventListener("submit", async (e) => {
      e.preventDefault();
      const valores = {}; $$("[data-cfg]", box).forEach((i) => (valores[i.dataset.cfg] = i.value));
      const b = $("#btn-cfg"); carregando(b, true);
      try { await api("/api/admin/config", { method: "PUT", body: { valores } }); toast("Configurações salvas", "ok"); admConfig(); }
      catch (err) { toast(err.message, "erro"); } finally { carregando(b, false); }
    });
  } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}
