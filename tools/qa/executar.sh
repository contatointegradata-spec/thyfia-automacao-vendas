#!/bin/bash
# Varredura de layout em todos os tamanhos de janela. Uso: tools/qa/executar.sh [larguras...]
# (padrão: 1210 1440 1024 768 390). Reinicia o servidor de teste a cada largura para zerar o estado do QR.
set -e
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python3}
LARGURAS=${@:-1210 1440 1024 768 390}
for W in $LARGURAS; do
  pkill -f tools/qa/servidor_qa.py 2>/dev/null || true; sleep 1
  ( nohup $PY tools/qa/servidor_qa.py > /tmp/thyfia_qa_servidor.log 2>&1 & )
  for i in $(seq 1 25); do curl -s --noproxy '*' -o /dev/null http://127.0.0.1:${QA_PORTA:-8767}/ && break; sleep 1; done
  QA_CAPTURAS=qa_capturas $PY tools/qa/varrer_layout.py qa_resultado_$W.json $W
done
pkill -f tools/qa/servidor_qa.py 2>/dev/null || true
