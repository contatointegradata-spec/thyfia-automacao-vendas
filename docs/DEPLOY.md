# Deploy da ThyfIA na VPS (Hostinger) com GitHub Actions

Fluxo: `git push` na `main` → testes → imagem Docker (GHCR) → SSH na VPS → `docker compose pull && up -d`.

Domínio: `https://thyfia.integradata.app.br` (registro A → 2.25.185.223).

## Uma vez só
1. **VPS:** rodar `deploy/setup-vps.sh` (cria usuário `deploy`, `/opt/thyfia`, chave SSH do Actions e mostra o proxy em uso).
2. **VPS:** preencher `/opt/thyfia/.env` (modelo: `.env.example`). Em produção inclua:
   `THYFIA_URL_PUBLICA=https://thyfia.integradata.app.br`, `PROXY_NETWORK` (rede do nginx), `THYFIA_ADMIN_EMAIL` (seu e-mail de dono) e, de preferência, `THYFIA_SECRET_KEY` (chave que cifra os tokens dos clientes; guarde uma cópia, pois trocar depois invalida os tokens salvos).
   Kiwify, n8n, Evolution, Telegram e suporte **não ficam mais no .env**: são configurados em tela, em **Administração → Configurações** (menu visível só para o administrador).
3. **GitHub → Settings → Secrets and variables → Actions:** `VPS_HOST` (2.25.185.223), `VPS_USER` (`deploy`), `VPS_SSH_KEY` (chave privada do passo 1), `VPS_PORT` (opcional, padrão 22).
4. **Extensão ThyfIA Conector:** recarregar em `chrome://extensions` (o manifesto agora aceita o domínio de produção).

5. **nginx da VPS:** rodar `deploy/add-nginx-thyfia.sh` (acrescenta os blocos de `deploy/nginx-thyfia.conf` ao nginx.conf com backup/rollback) e emitir um certificado próprio: `certbot certonly --webroot -w /var/www/certbot --cert-name thyfia.integradata.app.br -d thyfia.integradata.app.br`.

## Observações
- No servidor não existe janela do Chrome: o login do Mercado Livre é feito pela extensão (botão conectar/sincronizar), que envia a sessão para `/api/ml/sessao`.
- Dados (banco, fotos, perfis) ficam em `/opt/thyfia/dados` e sobrevivem aos deploys. Faça backup dessa pasta.
- Rollback: `docker compose` com a tag antiga, ex.: `image: ghcr.io/<repo>:<sha-anterior>`.

## Plataforma multi-cliente (planos + Kiwify)
1. Entre com a conta do dono (`THYFIA_ADMIN_EMAIL`): o menu **Administração** aparece.
2. **Administração → Configurações:** token do webhook da Kiwify, nome (ou ID) do plano de assinatura na Kiwify para Plus, Pro e Premium (funciona com 1 produto de 3 planos ou 1 produto por plano), links de checkout, webhook/chave do n8n, Evolution (URL, chave, instância), bot do Telegram e WhatsApp do suporte.
3. **Kiwify → Apps → Webhooks:** URL `https://thyfia.integradata.app.br/api/kiwify/webhook`, o mesmo token, os 3 produtos e os eventos: compra aprovada, reembolsada, chargeback, assinatura cancelada, atrasada e renovada.
4. **Administração → Pagamentos:** use *Simular compra* para testar o vínculo produto → plano e acompanhe os últimos eventos recebidos.
5. O cliente compra com o **mesmo e-mail** da conta; o plano libera sozinho. Acesso manual (cortesia/teste): Administração → Clientes → *Liberar plano*.

## Conta de administrador (dono)
No `/opt/thyfia/.env` da VPS:
```
THYFIA_ADMIN_EMAIL=seu@email.com
THYFIA_ADMIN_SENHA=uma-senha-forte
```
Depois `cd /opt/thyfia && docker compose up -d`. Na subida a conta é criada (ou a senha é redefinida, se já existir) e vira administradora. Entre pela tela normal e **apague a linha `THYFIA_ADMIN_SENHA`** do `.env` (senão a senha volta a esse valor a cada reinício).
