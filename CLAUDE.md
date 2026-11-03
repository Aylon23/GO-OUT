# Benchmark

Local, free portfolio tracker for one family. Backend: FastAPI + SQLModel (`backend/`), frontend: React + Vite (`frontend/`).

**Every step follows [docs/RULES.md](docs/RULES.md).** Read it before starting any work.

- Tests: `cd backend && ../.venv/bin/pytest -q`; frontend: `cd frontend && npx tsc --noEmit && npx vite build`.
- Offline price data for checks: `PORTFOLIO_PROVIDER=synthetic` (Yahoo is often unreachable in sandboxes).
- Never commit the user's data (`data/*.db`, Excel files, `.env`, `backups/`).
