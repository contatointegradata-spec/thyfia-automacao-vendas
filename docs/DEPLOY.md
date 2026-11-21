# Deploy da ThyfIA na VPS (Hostinger) com GitHub Actions

Fluxo: `git push` na `main` → testes → imagem Docker (GHCR) → SSH na VPS → `docker compose pull && up -d`.

Domínio: `https://thyfia.integradata.app.br` (registro A → 2.25.185.223).

## Uma vez só
1. **VPS:** rodar `deploy/setup-vps.sh` (cria usuário `deploy`, `/opt/thyfia`, chave SSH do Actions e mostra o proxy em uso).
2. **VPS:** preencher `/opt/thyfia/.env` (modelo: `.env.example`). Em produção inclua:
   `THYFIA_URL_PUBLICA=https://thyfia.integradata.app.br`, `PROXY_NETWORK` (rede do nginx), `N8N_WEBHOOK_URL`, `N8N_API_KEY`, `IG_ACCESS_TOKEN`, `IG_USER_ID`.
3. **GitHub → Settings → Secrets and variables → Actions:** `VPS_HOST` (2.25.185.223), `VPS_USER` (`deploy`), `VPS_SSH_KEY` (chave privada do passo 1), `VPS_PORT` (opcional, padrão 22).
4. **Extensão ThyfIA Conector:** recarregar em `chrome://extensions` (o manifesto agora aceita o domínio de produção).

5. **nginx da VPS:** adicionar `deploy/nginx-thyfia.conf` (server block com proxy para `thyfia:8000`) e emitir o certificado de `thyfia.integradata.app.br`.

## Observações
- No servidor não existe janela do Chrome: o login do Mercado Livre é feito pela extensão (botão conectar/sincronizar), que envia a sessão para `/api/ml/sessao`.
- Dados (banco, fotos, perfis) ficam em `/opt/thyfia/dados` e sobrevivem aos deploys. Faça backup dessa pasta.
- Rollback: `docker compose` com a tag antiga, ex.: `image: ghcr.io/<repo>:<sha-anterior>`.
