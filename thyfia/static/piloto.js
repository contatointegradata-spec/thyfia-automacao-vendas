/* =====================================================================
   ThyfIA — Piloto Automático (carregado depois de planos.js: usa $, $$, api, toast, estado, esc, brl…)
   ===================================================================== */
const piloto = { dados: null, timer: null, aguardandoCodigo: {} };

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

function plMsg(t, tipo) { const m = $("#pl-msg"); m.textContent = t || ""; m.className = "envio-msg " + (tipo || ""); }
function plHora(iso) { return iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : ""; }

async function abrirPiloto() {
  clearInterval(piloto.timer);
  await carregarPiloto();
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

  renderDestinos(d);
  if (!silencioso || !$("#form-pl-config").contains(document.activeElement)) preencherRegras(c);
  renderEstoque(d);
  renderAprovacao(d);
  renderRecentes(d);
  atualizarDica();
}

function renderDestinos(d) {
  const box = $("#pl-destinos");
  if (box.contains(document.activeElement) && document.activeElement.classList.contains("pl-cod")) return;  // não atrapalha quem digita o código
  if (!d.destinos.length) { box.innerHTML = `<p class="pl-vazio">Nenhum destino ainda. Adicione o primeiro abaixo.</p>`; return; }
  box.innerHTML = d.destinos.map((x) => {
    const t = PL_TIPOS[x.tipo];
    const tag = !x.verificado ? `<span class="pl-tag espera">aguardando código</span>` : x.ativo ? `<span class="pl-tag ok">ativo</span>` : `<span class="pl-tag">desligado</span>`;
    const exp = x.experimental ? `<span class="pl-tag exp">experimental</span>` : "";
    const acoes = !x.verificado
      ? `<input class="pl-cod" inputmode="numeric" maxlength="4" placeholder="0000" data-cod="${x.id}" aria-label="Código recebido no destino"><button class="btn btn-primario btn-mini" data-confirmar="${x.id}" type="button"><span class="btn-txt">Confirmar</span><span class="spinner"></span></button>`
      : `<button class="btn btn-fantasma btn-mini" data-alternar="${x.id}" data-ativo="${x.ativo ? 0 : 1}" type="button">${x.ativo ? "Pausar" : "Ligar"}</button>`;
    return `<div class="pl-destino ${x.ativo || !x.verificado ? "" : "off"}"><div class="pl-d-info"><b>${esc(x.nome || x.ref)}</b><small>${esc(t.nome)} · ${esc(x.ref)}</small>${x.erro ? `<div class="pl-d-erro">${esc(x.erro)}</div>` : ""}</div>${exp}${tag}<div class="pl-d-acoes">${acoes}<button class="btn btn-fantasma btn-mini" data-remover="${x.id}" type="button" aria-label="Remover destino">✕</button></div></div>`;
  }).join("");
}

function preencherRegras(c) {
  $("#pl-hora-ini").value = c.hora_ini; $("#pl-hora-fim").value = c.hora_fim;
  $("#pl-intervalo").value = c.intervalo_min; $("#pl-max-dia").value = c.max_dia; $("#pl-validade").value = c.validade_dias;
  $("#pl-pmin").value = c.preco_min ?? ""; $("#pl-pmax").value = c.preco_max ?? "";
  $("#pl-estilo").value = c.estilo; $("#pl-modo").value = c.modo;
  $("#pl-palavras").value = (c.palavras || []).join(", "); $("#pl-relatorio").checked = c.relatorio;
}

function renderEstoque(d) {
  const e = d.estoque, box = $("#pl-estoque");
  if (!e.total) { box.innerHTML = `<p class="pl-vazio">Sem ofertas novas dentro das suas regras. Gere mais links em <b>Gerar links</b> — o Piloto avisa no seu WhatsApp quando o estoque acaba.</p>`; return; }
  box.innerHTML = e.proximas.map((o) =>
    `<div class="pl-oferta"><span class="pl-pontos" title="Pontuação do Cérebro">${o.pontos}</span><b>${esc(o.titulo)}</b><small>${brl(o.preco)} · ${esc(o.busca)}</small><small>Por que o Cérebro gosta: ${esc(o.motivos.join("; ") || "melhor pontuação disponível")}</small></div>`).join("")
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
    `<div class="pl-linha"><span class="pl-t"><b>${esc(r.titulo)}</b><br><span class="pl-q">${esc(r.destino_nome || r.destino_ref || "")}${r.erro ? " · " + esc(r.erro) : ""}</span></span><span class="pl-tag ${r.status === "enviado" ? "ok" : r.status === "erro" ? "" : "espera"}">${nomes[r.status] || r.status}</span><span class="pl-q">${plHora(r.enviado_em || r.criado_em)}</span></div>`).join("");
}

function atualizarDica() {
  const t = PL_TIPOS[$("#pl-tipo").value], p = piloto.dados?.plataforma || {};
  $("#pl-entrada-rotulo").textContent = t.rotulo; $("#pl-entrada").placeholder = t.ph;
  const tipo = $("#pl-tipo").value;
  $("#pl-dica").textContent = tipo === "telegram"
    ? `Antes: adicione o bot ${p.bot || "da ThyfIA"} como administrador do canal, com permissão de publicar.`
    : `Antes: adicione o número ${p.numero ? "+" + p.numero : "da ThyfIA"} ao ${tipo === "whatsapp_grupo" ? "grupo" : "canal"} como administrador. Um código de 4 dígitos será enviado lá — digite-o aqui para confirmar.`;
}

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
    await api("/api/piloto/destinos", { body: { tipo: $("#pl-tipo").value, entrada } });
    $("#pl-entrada").value = "";
    plMsg("Código enviado ao destino. Abra lá, veja os 4 dígitos e digite aqui em “Confirmar”.", "ok");
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

$("#form-pl-config").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#btn-pl-salvar"), num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
  carregando(btn, true);
  await salvarPiloto({
    hora_ini: num("#pl-hora-ini"), hora_fim: num("#pl-hora-fim"), intervalo_min: num("#pl-intervalo"), max_dia: num("#pl-max-dia"),
    validade_dias: num("#pl-validade"), preco_min: num("#pl-pmin"), preco_max: num("#pl-pmax"), estilo: $("#pl-estilo").value,
    modo: $("#pl-modo").value, relatorio: $("#pl-relatorio").checked,
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
