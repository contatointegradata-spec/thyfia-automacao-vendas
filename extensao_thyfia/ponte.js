// Ponte entre a página da ThyfIA e a extensão (roda só em localhost:8000).
document.documentElement.dataset.thyfiaExt = chrome.runtime.getManifest().version;

window.addEventListener("message", (e) => {
  if (e.source !== window || !e.data || e.data.origem !== "thyfia-pagina") return;
  const { id, acao, dados } = e.data;
  if (!["conectar", "sincronizar", "buscar", "gerar", "gerar_fim"].includes(acao)) return;
  chrome.runtime.sendMessage({ acao, ...(dados || {}) }, (resp) => {
    const erro = chrome.runtime.lastError;
    window.postMessage(
      { origem: "thyfia-ext", id, ...(erro ? { ok: false, erro: erro.message } : resp) },
      location.origin
    );
  });
});
