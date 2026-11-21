#!/usr/bin/env bash
# Acrescenta os blocos da ThyfIA no nginx.conf da VPS (com backup e rollback). Rodar como root.
#   bash add-nginx-thyfia.sh
set -euo pipefail
CONF=/root/clinicmedia/nginx.conf
NGINX=clinicmedia_nginx
BLOCOS="$(dirname "$0")/nginx-thyfia.conf"

[ -f "$BLOCOS" ] || { echo "Falta $BLOCOS (baixe junto deste script)."; exit 1; }
grep -q "thyfia.integradata.app.br" "$CONF" && { echo "Blocos da ThyfIA já existem em $CONF."; exit 0; }

cp "$CONF" "$CONF.bak-thyfia-$(date +%Y%m%d-%H%M%S)"
python3 - "$CONF" "$BLOCOS" <<'PY'
import sys
conf, blocos = sys.argv[1], open(sys.argv[2]).read()
s = open(conf).read().rstrip()
assert s.endswith("}"), "nginx.conf inesperado"
s = s[:-1].rstrip() + "\n\n" + blocos.rstrip() + "\n}\n"   # antes do } final do http{}
open(conf, "w").write(s)
PY

if docker exec "$NGINX" nginx -t; then
  docker exec "$NGINX" nginx -s reload
  echo "OK: nginx recarregado com os blocos da ThyfIA."
else
  cp "$(ls -t "$CONF".bak-thyfia-* | head -1)" "$CONF"
  echo "ERRO no teste do nginx: configuração anterior restaurada, nada foi recarregado."
  exit 1
fi
