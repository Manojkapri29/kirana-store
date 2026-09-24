# Setup

## Local development (no Docker, no database server)

Requirements: Python 3.11+ (developed on 3.13), Node 20+ (developed on 22).

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head          # creates backend/data/kirana.db
python -m app.seed            # development shop + owner; prints the owner password once
uvicorn app.main:app --reload # http://127.0.0.1:8000

cd ../frontend
npm install && cp .env.example .env.local && npm run dev   # http://localhost:5173
```

Sign in as `owner@dev.kirana.local` with the printed password. To choose your own, set `KIRANA_DEV_OWNER_PASSWORD` before seeding.

## Checks

```bash
cd backend && pytest && ruff check . && alembic check
cd frontend && npx tsc -b && npm run lint && npm test && npm run build
```

## A real (non-development) shop

A production database is never seeded. Create the first shop and owner with the operator tool; it prints a one-time password:

```bash
python -m app.account_cli create-shop --shop-name "Sharma Store" --owner-email owner@example.com --owner-name "R Sharma"
```

A new shop starts on the Free plan with no categories. Assign a plan with `python -m app.subscription_admin assign --shop 1 --plan pro`
(this tool refuses to run when `KIRANA_ENVIRONMENT=production`; set `development` deliberately for that one command against the same database).
There is no payment step anywhere in the product.

See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md), [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) and [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).
