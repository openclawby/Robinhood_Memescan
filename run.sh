#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q "fastapi" "uvicorn[standard]" "httpx"

[ -f .env ] || { echo "CLAWBY_API_KEY=pk_xxx" > .env; echo "PORT=8799" >> .env; echo ">>> edit .env and set CLAWBY_API_KEY"; }
set -a; source .env; set +a

# The app's httpx (Blockscout/Clawby) must NOT use a proxy, but the `claude` CLI
# subprocess (deep analysis) DOES need it — save it for analyze.py, then strip it.
export SAVED_HTTP_PROXY="${http_proxy:-${HTTP_PROXY:-}}"
export SAVED_HTTPS_PROXY="${https_proxy:-${HTTPS_PROXY:-}}"
export SAVED_ALL_PROXY="${all_proxy:-${ALL_PROXY:-}}"
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy

echo ">>> dashboard on http://127.0.0.1:${PORT:-8799}"
exec ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port "${PORT:-8799}"
