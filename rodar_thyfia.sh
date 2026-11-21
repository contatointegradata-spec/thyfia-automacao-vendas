#!/bin/zsh
# Inicia a ThyfIA em http://localhost:8000
cd "$(dirname "$0")"
( sleep 2 && open "http://localhost:8000" ) &
exec .venv/bin/python -m uvicorn thyfia.app:app --host 127.0.0.1 --port 8000
