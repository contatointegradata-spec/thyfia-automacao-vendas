#!/usr/bin/env bash
# Preparação ÚNICA da VPS para receber o deploy da ThyfIA. Rodar como root na VPS:
#   bash setup-vps.sh
set -euo pipefail

PASTA=/opt/thyfia
USUARIO=deploy

command -v docker >/dev/null || { echo "Docker não encontrado."; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 não encontrado."; exit 1; }

echo "== Proxy reverso em uso (precisa dos valores abaixo para o .env) =="
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' | grep -Ei 'traefik|caddy|nginx|proxy' || \
  echo "(nenhum proxy reconhecido — veja 'docker ps' e me avise)"
for c in $(docker ps --format '{{.Names}}' | grep -i traefik || true); do
  echo "-- redes de $c:"
  docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c"
  echo "-- certresolver(s) em $c:"
  docker inspect -f '{{join .Args " "}} {{join .Config.Cmd " "}}' "$c" | tr ' ' '\n' | grep -i certificatesresolvers | head -3 || true
done

echo "== Usuário de deploy =="
id "$USUARIO" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USUARIO"
usermod -aG docker "$USUARIO"

echo "== Pasta $PASTA =="
mkdir -p "$PASTA/dados"
chown -R "$USUARIO":"$USUARIO" "$PASTA"
[ -f "$PASTA/.env" ] || { touch "$PASTA/.env"; chmod 600 "$PASTA/.env"; chown "$USUARIO":"$USUARIO" "$PASTA/.env"; }

echo "== Chave SSH exclusiva do GitHub Actions =="
SSH_DIR=/home/$USUARIO/.ssh
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "$SSH_DIR"
if [ ! -f "$SSH_DIR/github_actions" ]; then
  sudo -u "$USUARIO" ssh-keygen -t ed25519 -N "" -C "github-actions-thyfia" -f "$SSH_DIR/github_actions" >/dev/null
  cat "$SSH_DIR/github_actions.pub" >> "$SSH_DIR/authorized_keys"
  chown "$USUARIO":"$USUARIO" "$SSH_DIR/authorized_keys"; chmod 600 "$SSH_DIR/authorized_keys"
fi
echo
echo "Copie TODO o bloco abaixo (inclusive BEGIN/END) para o secret VPS_SSH_KEY do GitHub:"
echo "----------------------------------------------------------------"
cat "$SSH_DIR/github_actions"
echo "----------------------------------------------------------------"
echo "Depois de salvar no GitHub, apague a chave privada da VPS:  shred -u $SSH_DIR/github_actions"
echo
echo "Próximo passo: preencher $PASTA/.env (veja .env.example do repositório)."
