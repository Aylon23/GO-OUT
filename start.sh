#!/usr/bin/env bash
# One command to run everything.
#   ./start.sh          dev mode: API on :8000 + Vite on :5173 (open http://localhost:5173)
#   ./start.sh --prod   build the frontend and serve everything from http://localhost:8000
#   ./start.sh --test   run the backend test suite
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  echo "Creating Python virtual environment"
  "$PY" -m venv .venv
fi
if ! cmp -s backend/requirements.txt .venv/.deps-ok; then
  echo "Installing Python packages"
  .venv/bin/pip install -q -r backend/requirements.txt
  cp backend/requirements.txt .venv/.deps-ok
fi
if [ ! -d frontend/node_modules ] || [ frontend/package.json -nt frontend/node_modules ]; then
  echo "Installing frontend packages"
  (cd frontend && npm install --no-fund --no-audit)
  touch frontend/node_modules
fi

case "${1:-}" in
  --test)
    cd backend && exec ../.venv/bin/python -m pytest -q
    ;;
  --prod)
    (cd frontend && npm run build)
    cd backend && exec ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
    ;;
  *)
    (cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) &
    API_PID=$!
    trap 'kill $API_PID 2>/dev/null' EXIT INT TERM
    cd frontend && npx vite --host 127.0.0.1 --port 5173
    ;;
esac
