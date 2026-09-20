# Kirana Store

Inventory and business management for small Indian grocery (kirana) shops: stock, purchases,
detailed and quick sales, returns, suppliers, customer khata (credit), expenses, reports and exports.
Built to start as a single-shop app and grow into a multi-shop SaaS.

> **Status: Phase 2 of 16 (database foundation).** The API answers `GET /health`, the UI shell renders,
> and the SQLite database, models and migration are in place. There are no business features yet:
> nothing reads or writes the database from the app. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS 4, react-i18next (English/Hindi) |
| Backend | Python, FastAPI, Pydantic settings |
| Database | SQLAlchemy 2 + Alembic on SQLite for the MVP; PostgreSQL later (nothing to install now) |

Recharts is added in Phase 12, when the first chart is built.

## Prerequisites

- Python 3.11+ (developed on 3.13)
- Node.js 20+ (developed on 22) and npm

No database server and no Docker are needed.

## Quick start

Run the backend and the frontend in two terminals, starting from this folder.

**Backend** (http://127.0.0.1:8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head        # creates backend/data/kirana.db (git-ignored)
python -m app.seed          # optional: development shop + owner user
uvicorn app.main:app --reload
```

Check it: `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`.
Interactive API docs are at http://127.0.0.1:8000/docs (development only).

**Frontend** (http://localhost:5173)

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

In development the Vite dev server forwards `/api` and `/health` to the backend, so the browser
needs no CORS setup. The header shows a green "Server connected" badge when the backend is reachable.

## Checks

```bash
# Backend (from backend/, venv active)
pytest                 # tests
alembic check          # models and migrations agree
ruff check .           # lint
ruff format --check .  # formatting

# Frontend (from frontend/)
npm run typecheck      # TypeScript
npm run lint           # oxlint
npm run build          # type-check + production build
```

## Project layout

```
kirana-store/
├── backend/     FastAPI app (app/), migrations/, tests/, requirements, .env.example
├── frontend/    React + Vite + TypeScript app (src/), .env.example
└── docs/        ARCHITECTURE, DATABASE, BUSINESS_RULES, ROADMAP
```

## Configuration

All configuration comes from environment variables; nothing secret is committed.

- Backend: `backend/.env` (copy from `.env.example`), variables prefixed `KIRANA_`.
- Frontend: `frontend/.env.local` (copy from `.env.example`), variables prefixed `VITE_`.
  Anything prefixed `VITE_` is visible to the browser, so never put secrets there.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): layers, service boundaries, ledgers, SaaS readiness
- [Database](docs/DATABASE.md): planned schema and SQLite to PostgreSQL strategy
- [Business rules](docs/BUSINESS_RULES.md): the approved rules every phase must follow
- [Roadmap](docs/ROADMAP.md): Phases 1 to 16
