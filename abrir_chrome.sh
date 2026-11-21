#!/bin/zsh
# Abre o Google Chrome com a porta de depuração 9222 para a automação se conectar.
# O Chrome exige um perfil separado para isso; faça login no Mercado Livre nele uma vez.
PERFIL="$(cd "$(dirname "$0")" && pwd)/perfil_chrome"
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$PERFIL" \
  "https://www.mercadolivre.com.br/afiliados/hub?is_affiliate=true"
