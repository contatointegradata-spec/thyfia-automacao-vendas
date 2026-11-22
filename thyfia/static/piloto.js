/* =====================================================================
   ThyfIA — Piloto Automático (carregado depois de planos.js: usa $, $$, api, toast, estado, esc, brl…)
   ===================================================================== */
const piloto = { nichoDe: {}, dados: null, timer: null, waTimer: null, qr: "", qrEm: 0, escolhendo: false, grupos: [], sel: new Set(), filtro: "", waAssin: "" };

const PL_TIPOS = {
  whatsapp_grupo: { nome: "Grupo de WhatsApp", rotulo: "Link de convite do grupo", ph: "https://chat.whatsapp.com/…" },
  whatsapp_canal: { nome: "Canal de WhatsApp", rotulo: "ID do canal (…@newsletter)", ph: "120363…@newsletter" },
  telegram: { nome: "Canal do Telegram", rotulo: "@ do canal", ph: "@minhasofertas" },
};
const PL_ACOES = {
  planejou: "Oferta escolhida pelo Cérebro — saindo para os destinos.",
  aguardando_aprovacao: "Oferta escolhida — aguardando a sua aprovação abaixo.",
  sem_destinos: "Adicione e confirme pelo menos um destino primeiro.",
  sem_estoque: "Não há ofertas novas para divulgar. Gere mais links.",
  ciclo_em_andamento: "Já há uma oferta saindo agora. Aguarde terminar.",
  pausado: "O Piloto está em pausa temporária.",
  desligado: "Ligue o Piloto primeiro.",
};

function plNichos() { return piloto.dados?.nichos || [{ slug: "geral", nome: "Todos os nichos", emoji: "🌐" }]; }
function plOpcoesNicho(sel) { return plNichos().map((n) => `<option value="${esc(n.slug)}" ${n.slug === sel ? "selected" : ""}>${n.emoji} ${esc(n.nome)}</option>`).join(""); }
function plNicho(slug) { return plNichos().find((n) => n.slug === slug) || { nome: "Sem nicho", emoji: "•" }; }
function plMsg(t, tipo) { const m = $("#pl-msg"); m.textContent = t || ""; m.className = "envio-msg " + (tipo || ""); }
function plHora(iso) { return iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : ""; }

async function abrirPiloto() {
  clearInterval(piloto.timer);
  await carregarPiloto();
  verificarWhatsapp();
  piloto.timer = setInterval(() => { if (estado.view === "piloto" && !document.hidden) carregarPiloto(true); else if (estado.view !== "piloto") clearInterval(piloto.timer); }, 15000);
}

async function carregarPiloto(silencioso = false) {
  try {
    piloto.dados = await api("/api/piloto");
    renderPiloto(silencioso);
  } catch (e) { if (!silencioso) plMsg(e.message, "erro"); }
}

function renderPiloto(silencioso) {
  const d = piloto.dados, c = d.config;
  // topo
  $("#pl-ativo").checked = c.ativo;
  $("#pl-ativo-txt").textContent = c.ativo ? "Piloto ligado" : "Piloto desligado";
  const prontos = d.destinos.filter((x) => x.verificado && x.ativo).length;
  let sit;
  if (!c.ativo) sit = "Ligue para as ofertas saírem sozinhas, dentro do horário que você definir.";
  else if (c.pausa_ate && new Date(c.pausa_ate) > new Date(d.agora)) sit = `Em pausa até ${plHora(c.pausa_ate)}: ${c.pausa_motivo || "problema temporário"}.`;
  else if (!prontos) sit = "Ligado, mas sem nenhum destino confirmado ainda.";
  else if (c.proximo_envio && new Date(c.proximo_envio) > new Date(d.agora)) sit = `Próxima oferta por volta das ${plHora(c.proximo_envio)}.`;
  else sit = "Procurando a próxima oferta…";
  $("#pl-situacao").textContent = sit;
  $("#pl-numeros").innerHTML =
    `<div class="pl-num"><strong>${d.hoje.ofertas}</strong><span>ofertas hoje</span></div>` +
    `<div class="pl-num"><strong>${d.hoje.mensagens}</strong><span>mensagens enviadas</span></div>` +
    `<div class="pl-num ${d.estoque.baixo ? "alerta" : ""}"><strong>${d.estoque.total}</strong><span>no estoque</span></div>` +
    (d.hoje.erros ? `<div class="pl-num alerta"><strong>${d.hoje.erros}</strong><span>com erro</span></div>` : "");

  // aviso de plataforma
  const av = $("#pl-aviso-plataforma"), p = d.plataforma;
  const falta = [!p.whatsapp && "WhatsApp", !p.telegram && "Telegram"].filter(Boolean);
  av.hidden = !falta.length;
  if (falta.length) av.textContent = `A ThyfIA ainda não habilitou o envio por ${falta.join(" e ")}. Se for o canal que você quer usar, fale com o suporte.`;

  renderWhatsapp();
  renderNichos(d);
  renderDestinos(d);
  if (!silencioso || !$("#form-pl-config").contains(document.activeElement)) preencherRegras(c);
  renderEstoque(d);
  renderAprovacao(d);
  renderRecentes(d);
  atualizarDica();
}

function renderNichos(d) {
  const sel = $("#pl-nicho-novo"), atual = sel.value || "geral";
  if (sel.dataset.n !== String(plNichos().length)) { sel.innerHTML = plOpcoesNicho(atual); sel.dataset.n = String(plNichos().length); }
  const box = $("#pl-cobertura"), cob = (d.cobertura || []).filter((x) => x.nicho !== "geral");
  box.hidden = !cob.length;
  box.innerHTML = cob.map((x) => `<span class="pl-chip ${x.ofertas < 2 ? "alerta" : ""}" title="${x.ofertas} oferta(s) pronta(s) para este nicho">${x.emoji} ${esc(x.nome)} · ${x.grupos} grupo(s) · ${x.ofertas} oferta(s)${x.ofertas < 2 ? " ⚠️" : ""}</span>`).join("")
    + (cob.some((x) => x.ofertas < 2) ? `<p class="mini">⚠️ Poucas ofertas nesse nicho. Gere links com buscas dele (ex.: “whey”, “air fryer”) para o Piloto ter o que enviar.</p>` : "");
}

function renderDestinos(d) {
  const box = $("#pl-destinos");
  if (box.contains(document.activeElement) && (document.activeElement.classList.contains("pl-cod") || document.activeElement.classList.contains("pl-nicho"))) return;  // não atrapalha quem digita o código
  if (!d.destinos.length) { box.innerHTML = `<p class="pl-vazio">Nenhum destino ainda. Conecte o WhatsApp e escolha seus grupos, ou adicione um Telegram abaixo.</p>`; return; }
  box.innerHTML = d.destinos.map((x) => {
    const t = PL_TIPOS[x.tipo];
    const tag = !x.verificado ? `<span class="pl-tag espera">aguardando código</span>` : x.ativo ? `<span class="pl-tag ok">ativo</span>` : `<span class="pl-tag">desligado</span>`;
    const exp = (x.experimental ? `<span class="pl-tag exp">experimental</span>` : "") + (x.proprio ? `<span class="pl-tag">meu WhatsApp</span>` : "");
    const acoes = !x.verificado
      ? `<input class="pl-cod" inputmode="numeric" maxlength="4" placeholder="0000" data-cod="${x.id}" aria-label="Código recebido no destino"><button class="btn btn-primario btn-mini" data-confirmar="${x.id}" type="button"><span class="btn-txt">Confirmar</span><span class="spinner"></span></button>`
      : `<button class="btn btn-fantasma btn-mini" data-alternar="${x.id}" data-ativo="${x.ativo ? 0 : 1}" type="button">${x.ativo ? "Pausar" : "Ligar"}</button>`;
    return `<div class="pl-destino ${x.ativo || !x.verificado ? "" : "off"}"><div class="pl-d-info"><b>${esc(x.nome || x.ref)}</b><small>${esc(t.nome)} · ${esc(x.ref)}</small><label class="pl-nicho-l">Nicho <select class="pl-nicho" data-nicho="${x.id}" aria-label="Nicho de ${esc(x.nome || x.ref)}">${plOpcoesNicho(x.nicho)}</select></label>${x.erro ? `<div class="pl-d-erro">${esc(x.erro)}</div>` : ""}</div>${exp}${tag}<div class="pl-d-acoes">${acoes}<button class="btn btn-fantasma btn-mini" data-remover="${x.id}" type="button" aria-label="Remover destino">✕</button></div></div>`;
  }).join("");
}

function preencherRegras(c) {
  $("#pl-hora-ini").value = c.hora_ini; $("#pl-hora-fim").value = c.hora_fim;
  $("#pl-intervalo").value = c.intervalo_min; $("#pl-max-dia").value = c.max_dia; $("#pl-validade").value = c.validade_dias;
  $("#pl-pmin").value = c.preco_min ?? ""; $("#pl-pmax").value = c.preco_max ?? "";
  $("#pl-estilo").value = c.estilo; $("#pl-modo").value = c.modo;
  $("#pl-palavras").value = (c.palavras || []).join(", "); $("#pl-relatorio").checked = c.relatorio;
  $("#pl-rastrear").checked = !!c.rastrear;
}

function renderEstoque(d) {
  const e = d.estoque, box = $("#pl-estoque");
  if (!e.total) { box.innerHTML = `<p class="pl-vazio">Sem ofertas novas dentro das suas regras. Gere mais links em <b>Gerar links</b> — o Piloto avisa no seu WhatsApp quando o estoque acaba.</p>`; return; }
  box.innerHTML = e.proximas.map((o) =>
    `<div class="pl-oferta"><span class="pl-pontos" title="Pontuação do Cérebro">${o.pontos}</span><b>${esc(o.titulo)}</b><small>${brl(o.preco)} · ${esc(o.busca)}</small><label class="pl-nicho-l">Nicho${o.nicho_origem ? ` <em>(${esc(o.nicho_origem)})</em>` : ""} <select class="pl-nicho" data-busca="${esc(o.busca)}" aria-label="Nicho da busca ${esc(o.busca)}"><option value="">${o.nicho ? "automático" : "sem nicho — escolher"}</option>${plNichos().filter((n) => n.slug !== "geral").map((n) => `<option value="${esc(n.slug)}" ${o.nicho === n.slug && o.nicho_origem === "correção sua" ? "selected" : ""}>${n.emoji} ${esc(n.nome)}</option>`).join("")}</select></label><small>Por que o Cérebro gosta: ${esc(o.motivos.join("; ") || "melhor pontuação disponível")}</small></div>`).join("")
    + (e.total > e.proximas.length ? `<p class="mini">+ ${e.total - e.proximas.length} oferta(s) na fila.</p>` : "");
}

function renderAprovacao(d) {
  const sec = $("#pl-aprovacao");
  sec.hidden = !d.aprovacao.length;
  $("#pl-aprovacao-lista").innerHTML = d.aprovacao.map((a) =>
    `<div class="pl-ap">${a.imagem ? `<img src="${esc(a.imagem)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}<pre>${esc(a.texto)}</pre><div class="pl-d-acoes"><button class="btn btn-primario btn-mini" data-aprovar="${a.item_id}" type="button">Aprovar e enviar (${a.destinos})</button><button class="btn btn-fantasma btn-mini" data-pular="${a.item_id}" type="button">Pular</button></div></div>`).join("");
}

function renderRecentes(d) {
  const box = $("#pl-recentes");
  if (!d.recentes.length) { box.innerHTML = `<p class="pl-vazio">Os envios aparecem aqui assim que o Piloto começar.</p>`; return; }
  const nomes = { enviado: "enviado", pendente: "na fila", aguardando: "aguardando", erro: "erro", pulado: "pulado" };
  box.innerHTML = d.recentes.map((r) =>
    `<div class="pl-linha"><span class="pl-t"><b>${esc(r.titulo)}</b><br><span class="pl-q">${esc(r.destino_nome || r.destino_ref || "")}${r.erro ? " · " + esc(r.erro) : ""}</span></span><span class="pl-tag ${r.status === "enviado" ? "ok" : r.status === "erro" ? "" : "espera"}">${nomes[r.status] || r.status}</span><span class="pl-q">${r.cliques ? r.cliques + " clique(s) · " : ""}${plHora(r.enviado_em || r.criado_em)}</span></div>`).join("");
}

function atualizarDica() {
  const t = PL_TIPOS[$("#pl-tipo").value], p = piloto.dados?.plataforma || {};
  $("#pl-entrada-rotulo").textContent = t.rotulo; $("#pl-entrada").placeholder = t.ph;
  const tipo = $("#pl-tipo").value;
  const conectado = piloto.dados?.whatsapp?.estado === "conectado";
  $("#pl-dica").textContent = tipo === "telegram"
    ? `Antes: adicione o bot ${p.bot || "da ThyfIA"} como administrador do canal, com permissão de publicar.`
    : tipo === "whatsapp_canal" && conectado
    ? "O canal precisa ser seu (você como administrador) e o ID termina em @newsletter. Como o seu WhatsApp está conectado, não precisa de código."
    : `Antes: adicione o número ${p.numero ? "+" + p.numero : "da ThyfIA"} ao ${tipo === "whatsapp_grupo" ? "grupo" : "canal"} como administrador. Um código de 4 dígitos será enviado lá — digite-o aqui para confirmar.`;
}

/* ---- WhatsApp do cliente: QR code e escolha de grupos ---- */
function pararWa() { clearInterval(piloto.waTimer); piloto.waTimer = null; }

async function verificarWhatsapp() {  // pergunta à Evolution se a conexão continua de pé
  try {
    const w = await api("/api/piloto/whatsapp");
    if (piloto.dados) { piloto.dados.whatsapp = w; renderWhatsapp(); }
  } catch (_) { /* silencioso: o painel mostra o último estado conhecido */ }
}

function renderWhatsapp(forcar) {
  const w = piloto.dados.whatsapp, plat = piloto.dados.plataforma, box = $("#pl-wa-corpo");
  if (piloto.escolhendo && !forcar) return;  // não atrapalha quem está marcando os grupos
  const nProprios = piloto.dados.destinos.filter((x) => x.proprio && x.tipo === "whatsapp_grupo").length;
  const assin = [w.estado, w.numero, !!piloto.qr, piloto.qr.slice(-12), nProprios, plat.qr].join("|");
  if (!forcar && assin === piloto.waAssin) return;
  piloto.waAssin = assin;
  if (!plat.qr) { box.innerHTML = `<p class="pl-aviso">A ThyfIA ainda não habilitou a conexão por QR code. Fale com o suporte.</p>`; return; }
  if (w.estado === "conectado") {
    pararWa();
    box.innerHTML = `<div class="pl-wa-ok"><span class="pl-tag ok">conectado</span><b>${w.numero ? "+" + esc(w.numero) : "WhatsApp conectado"}</b></div>
      <p class="mini">${nProprios ? `O Piloto publica em <b>${nProprios}</b> grupo(s) seu(s).` : "Agora escolha em quais grupos o Piloto vai publicar."}</p>
      <div class="au-acoes"><button class="btn btn-primario" id="btn-wa-grupos" type="button"><span class="btn-txt">${nProprios ? "Mudar grupos" : "Escolher grupos"}</span><span class="spinner"></span></button>
      <button class="btn btn-fantasma" id="btn-wa-sair" type="button">Desconectar</button></div>`;
  } else if (w.estado === "conectando" && piloto.qr) {
    box.innerHTML = `<div class="pl-qr"><img src="${esc(piloto.qr)}" alt="QR code para conectar o WhatsApp"><ol>
      <li>Abra o WhatsApp no celular</li><li>Toque em <b>Aparelhos conectados</b></li><li>Toque em <b>Conectar um aparelho</b> e aponte a câmera para o QR code</li></ol></div>
      <p class="mini">O código se renova sozinho. Esta tela detecta a conexão assim que você ler.</p>
      <div class="au-acoes"><button class="btn btn-fantasma" id="btn-wa-cancelar" type="button">Cancelar</button></div>`;
  } else {
    const caiu = w.estado === "desconectado" && (w.numero || nProprios);
    box.innerHTML = (caiu ? `<p class="pl-aviso">O seu WhatsApp está desconectado e o Piloto não consegue publicar. Leia o QR code de novo — os seus grupos continuam salvos.</p>` : "")
      + (w.erro ? `<p class="pl-aviso">${esc(w.erro)}</p>` : "")
      + `<div class="au-acoes"><button class="btn btn-primario" id="btn-wa-conectar" type="button"><span class="btn-txt">${caiu ? "Reconectar" : "Conectar meu WhatsApp"}</span><span class="spinner"></span></button></div>`;
  }
}

async function gerarQr(btn) {
  if (btn) carregando(btn, true);
  try {
    const r = await api("/api/piloto/whatsapp/conectar", { body: {} });
    piloto.dados.whatsapp = { ...piloto.dados.whatsapp, estado: r.estado, numero: r.numero || piloto.dados.whatsapp.numero };
    piloto.qr = r.qr || ""; piloto.qrEm = Date.now();
    renderWhatsapp(true);
    if (r.estado === "conectado") { await carregarPiloto(); return abrirGrupos(); }
    iniciarVigiaQr();
  } catch (err) { plMsg(err.message, "erro"); } finally { if (btn) carregando(btn, false); }
}

function iniciarVigiaQr() {
  pararWa();
  piloto.waTimer = setInterval(async () => {
    if (estado.view !== "piloto") return pararWa();
    try {
      if (Date.now() - piloto.qrEm > 40000) {  // o QR do WhatsApp vence em ~1 min: renova
        const r = await api("/api/piloto/whatsapp/conectar", { body: {} });
        piloto.qrEm = Date.now();
        if (r.estado !== "conectado") { piloto.qr = r.qr || piloto.qr; renderWhatsapp(true); return; }
      }
      const w = await api("/api/piloto/whatsapp");
      piloto.dados.whatsapp = w;
      if (w.estado === "conectado") { pararWa(); piloto.qr = ""; plMsg("WhatsApp conectado!", "ok"); await carregarPiloto(); abrirGrupos(); }
    } catch (_) { /* tenta de novo no próximo ciclo */ }
  }, 3000);
}

async function abrirGrupos() {
  const btn = $("#btn-wa-grupos"); if (btn) carregando(btn, true);
  try {
    piloto.grupos = await api("/api/piloto/whatsapp/grupos");
    piloto.sel = new Set(piloto.grupos.filter((g) => g.selecionado).map((g) => g.jid));
    piloto.nichoDe = Object.fromEntries(piloto.grupos.map((g) => [g.jid, g.nicho || "geral"]));
    piloto.filtro = ""; piloto.escolhendo = true;
    $("#pl-wa-corpo").innerHTML = `<h4 class="pl-sub">Em quais grupos o Piloto publica?</h4>
      <input type="text" id="pl-g-busca" class="pl-busca" placeholder="Buscar grupo…" autocomplete="off" aria-label="Buscar grupo">
      <div id="pl-g-lista" class="pl-grupos"></div>
      <div class="au-acoes"><button class="btn btn-primario" id="btn-g-salvar" type="button"><span class="btn-txt">Salvar grupos</span><span class="spinner"></span></button>
      <button class="btn btn-fantasma" id="btn-g-cancelar" type="button">Cancelar</button></div>`;
    renderGrupos();
  } catch (err) { plMsg(err.message, "erro"); } finally { if (btn) carregando(btn, false); }
}

function renderGrupos() {
  const f = piloto.filtro.toLowerCase();
  const lista = piloto.grupos.filter((g) => !f || g.nome.toLowerCase().includes(f));
  $("#pl-g-lista").innerHTML = lista.length ? lista.map((g) =>
    `<label class="pl-grupo"><input type="checkbox" data-jid="${esc(g.jid)}" ${piloto.sel.has(g.jid) ? "checked" : ""}><span class="auto-caixa" aria-hidden="true"></span><span class="pl-g-nome">${esc(g.nome)}</span>${g.tamanho ? `<small>${g.tamanho} membros</small>` : ""}<select class="pl-nicho" data-gnicho="${esc(g.jid)}" aria-label="Nicho de ${esc(g.nome)}">${plOpcoesNicho(piloto.nichoDe[g.jid])}</select></label>`).join("")
    : `<p class="pl-vazio">${piloto.grupos.length ? "Nenhum grupo com esse nome." : "Não encontrei grupos neste WhatsApp."}</p>`;
  $("#btn-g-salvar .btn-txt").textContent = `Salvar grupos (${piloto.sel.size})`;
}

$("#pl-wa-corpo").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "btn-wa-conectar") gerarQr(b);
  else if (b.id === "btn-wa-cancelar") { pararWa(); piloto.qr = ""; renderWhatsapp(true); }
  else if (b.id === "btn-wa-grupos") abrirGrupos();
  else if (b.id === "btn-g-cancelar") { piloto.escolhendo = false; renderWhatsapp(true); }
  else if (b.id === "btn-g-salvar") {
    carregando(b, true);
    try {
      piloto.dados.destinos = await api("/api/piloto/whatsapp/grupos", { method: "PUT", body: { jids: [...piloto.sel], nichos: Object.fromEntries([...piloto.sel].map((j) => [j, piloto.nichoDe[j] || "geral"])) } });
      piloto.escolhendo = false; plMsg(piloto.sel.size ? "Grupos salvos. O Piloto já pode publicar neles." : "Nenhum grupo selecionado.", "ok");
      await carregarPiloto(); renderWhatsapp(true);
    } catch (err) { plMsg(err.message, "erro"); carregando(b, false); }
  } else if (b.id === "btn-wa-sair") {
    if (!confirm("Desconectar o seu WhatsApp? O Piloto para de publicar nos seus grupos até você ler o QR de novo.")) return;
    try { await api("/api/piloto/whatsapp/desconectar", { body: {} }); piloto.qr = ""; await carregarPiloto(); renderWhatsapp(true); }
    catch (err) { plMsg(err.message, "erro"); }
  }
});
$("#pl-wa-corpo").addEventListener("change", (e) => {
  const n = e.target.closest("select[data-gnicho]");
  if (n) { piloto.nichoDe[n.dataset.gnicho] = n.value; if (n.value !== "geral") piloto.sel.add(n.dataset.gnicho); renderGrupos(); return; }
  const c = e.target.closest("input[data-jid]"); if (!c) return;
  c.checked ? piloto.sel.add(c.dataset.jid) : piloto.sel.delete(c.dataset.jid);
  $("#btn-g-salvar .btn-txt").textContent = `Salvar grupos (${piloto.sel.size})`;
});
$("#pl-wa-corpo").addEventListener("input", (e) => { if (e.target.id === "pl-g-busca") { piloto.filtro = e.target.value; renderGrupos(); } });

/* ---- ações ---- */
async function salvarPiloto(corpo, msgOk) {
  try { piloto.dados = await api("/api/piloto/config", { method: "PUT", body: corpo }); renderPiloto(); if (msgOk) plMsg(msgOk, "ok"); return true; }
  catch (e) { plMsg(e.message, "erro"); toast(e.message, "erro"); return false; }
}

$("#pl-ativo").addEventListener("change", async (e) => {
  const quer = e.target.checked;
  if (quer && !piloto.dados.destinos.some((x) => x.verificado && x.ativo)) toast("Adicione e confirme um destino para o Piloto ter onde publicar.", "info");
  if (!(await salvarPiloto({ ativo: quer }, quer ? "Piloto ligado." : "Piloto desligado."))) e.target.checked = !quer;
});

$("#pl-tipo").addEventListener("change", () => { $("#pl-entrada").value = ""; atualizarDica(); });

$("#form-pl-destino").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#btn-pl-add"), entrada = $("#pl-entrada").value.trim();
  if (!entrada) return plMsg("Informe o destino.", "erro");
  carregando(btn, true); plMsg("");
  try {
    const tipo = $("#pl-tipo").value, meu = tipo === "whatsapp_canal" && piloto.dados?.whatsapp?.estado === "conectado";
    await api("/api/piloto/destinos", { body: { tipo, entrada, meu_numero: meu, nicho: $("#pl-nicho-novo").value } });
    $("#pl-entrada").value = "";
    plMsg(meu ? "Canal adicionado." : "Código enviado ao destino. Abra lá, veja os 4 dígitos e digite aqui em “Confirmar”.", "ok");
    await carregarPiloto();
  } catch (err) { plMsg(err.message, "erro"); } finally { carregando(btn, false); }
});

$("#pl-destinos").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  try {
    if (b.dataset.confirmar) {
      const cod = $(`[data-cod="${b.dataset.confirmar}"]`).value.trim();
      if (cod.length !== 4) return plMsg("O código tem 4 dígitos.", "erro");
      carregando(b, true);
      try { await api(`/api/piloto/destinos/${b.dataset.confirmar}/confirmar`, { body: { codigo: cod } }); plMsg("Destino confirmado!", "ok"); } finally { carregando(b, false); }
    } else if (b.dataset.alternar) {
      await api(`/api/piloto/destinos/${b.dataset.alternar}`, { method: "PUT", body: { ativo: b.dataset.ativo === "1" } });
    } else if (b.dataset.remover) {
      if (!confirm("Remover este destino do Piloto?")) return;
      await api(`/api/piloto/destinos/${b.dataset.remover}`, { method: "DELETE" });
    } else return;
    await carregarPiloto();
  } catch (err) { plMsg(err.message, "erro"); }
});

$("#pl-destinos").addEventListener("change", async (e) => {
  const s = e.target.closest("select[data-nicho]"); if (!s) return;
  try { await api(`/api/piloto/destinos/${s.dataset.nicho}`, { method: "PUT", body: { nicho: s.value } }); plMsg(`Nicho alterado para ${plNicho(s.value).nome}.`, "ok"); await carregarPiloto(); }
  catch (err) { plMsg(err.message, "erro"); await carregarPiloto(); }
});

$("#pl-estoque").addEventListener("change", async (e) => {
  const s = e.target.closest("select[data-busca]"); if (!s) return;
  try { piloto.dados = await api("/api/piloto/nicho-busca", { method: "PUT", body: { busca: s.dataset.busca, nicho: s.value } }); renderPiloto(); plMsg("Nicho da busca atualizado — o Cérebro aprendeu com a sua correção.", "ok"); }
  catch (err) { plMsg(err.message, "erro"); }
});

$("#form-pl-config").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#btn-pl-salvar"), num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
  carregando(btn, true);
  await salvarPiloto({
    hora_ini: num("#pl-hora-ini"), hora_fim: num("#pl-hora-fim"), intervalo_min: num("#pl-intervalo"), max_dia: num("#pl-max-dia"),
    validade_dias: num("#pl-validade"), preco_min: num("#pl-pmin"), preco_max: num("#pl-pmax"), estilo: $("#pl-estilo").value,
    modo: $("#pl-modo").value, relatorio: $("#pl-relatorio").checked, rastrear: $("#pl-rastrear").checked,
    palavras: $("#pl-palavras").value.split(",").map((s) => s.trim()).filter(Boolean),
  }, "Regras salvas.");
  carregando(btn, false);
});

$("#btn-pl-agora").addEventListener("click", async () => {
  const btn = $("#btn-pl-agora"); carregando(btn, true); plMsg("");
  try {
    const r = await api("/api/piloto/enviar-agora", { body: {} });
    plMsg(PL_ACOES[r.acao] || "Pronto.", r.acao === "planejou" || r.acao === "aguardando_aprovacao" ? "ok" : "erro");
    await carregarPiloto();
  } catch (err) { plMsg(err.message, "erro"); } finally { carregando(btn, false); }
});

$("#pl-aprovacao").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  try {
    if (b.dataset.aprovar) { await api(`/api/piloto/aprovar/${b.dataset.aprovar}`, { body: {} }); plMsg("Aprovada: saindo para os destinos.", "ok"); }
    else if (b.dataset.pular) { await api(`/api/piloto/pular/${b.dataset.pular}`, { body: {} }); plMsg("Oferta pulada.", "ok"); }
    else return;
    await carregarPiloto();
  } catch (err) { plMsg(err.message, "erro"); }
});


/* ---- Cérebro: o que foi aprendido ---- */
async function carregarAprendizado() {
  let a; try { a = await api("/api/cerebro/aprendizado"); } catch (_) { return; }
  $("#ap-painel").hidden = false;
  const fases = { sem_dados: "ainda sem retorno do público", aprendendo: "aprendendo", calibrado: "calibrado" };
  $("#ap-fase").textContent = fases[a.fase] || a.fase;
  const barra = (titulo, linhas) => linhas.length ? `<div class="ap-bloco"><b>${titulo}</b>${linhas.map((l) => `<div class="ap-linha"><span>${esc(l.rotulo)}</span><i style="--w:${l.taxa}%"></i><small>${l.taxa}% · ${l.tentativas}</small></div>`).join("")}</div>` : "";
  const p = a.preferencias;
  $("#ap-publico").innerHTML = a.fase === "sem_dados"
    ? `<p class="muted">O Cérebro ainda não recebeu retorno do seu público. ${a.rastrear ? "Os cliques das ofertas já estão sendo medidos; em até 24 h ele começa a calibrar." : "No <b>Piloto Automático</b>, ligue <b>Medir cliques</b> (ou use o modo de aprovação): é assim que ele aprende o que o seu público quer."}</p>`
    : `<p class="mini">${a.cliques.acertos} de ${a.cliques.avaliados} envios avaliados tiveram retorno (${a.cliques.taxa}%).</p>` +
      barra("Por nicho", p.nicho) + barra("Por busca", p.busca) + barra("Por faixa de preço", p.faixa) + barra("Layout da mensagem", p.variante);
  const pal = Object.values(a.palavras_aprendidas);
  $("#ap-palavras").innerHTML = pal.length
    ? pal.map((n) => `<div class="ap-bloco"><b>${n.emoji} ${esc(n.nome)}</b><div>${n.palavras.map((w) => `<span class="pl-chip">${esc(w)}</span>`).join("")}</div></div>`).join("")
    : `<p class="muted">Ainda nenhuma. Conforme você busca, o Cérebro aprende que palavras novas indicam cada nicho.</p>`;
  $("#ap-quedas").innerHTML = a.precos.quedas.length
    ? a.precos.quedas.map((q) => `<div class="ap-queda"><b>${esc(q.titulo)}</b><small>de ${brl(q.de)} para ${brl(q.para)} · −${q.pct}%</small></div>`).join("")
    : `<p class="muted">${a.precos.produtos_com_historico ? "Nenhuma queda real nos últimos dias." : "Guardando o preço de cada produto dia a dia: as quedas aparecem aqui."}</p>`;
}
