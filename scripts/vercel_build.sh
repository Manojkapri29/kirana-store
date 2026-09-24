#!/usr/bin/env bash
# Vercel build: (1) bring the database to the current schema when one is configured, (2) build the React app.
# The database URL comes from the environment (DATABASE_URL / POSTGRES_URL from the Neon integration, or KIRANA_DATABASE_URL); it is never in Git.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${KIRANA_DATABASE_URL:-}${DATABASE_URL:-}${POSTGRES_URL:-}" ]; then
  echo "== migrating the database"
  python3 -m venv /tmp/kirana-venv
  /tmp/kirana-venv/bin/pip install --quiet -r backend/requirements.txt
  (cd backend && /tmp/kirana-venv/bin/python -m alembic upgrade head)
else
  echo "== no database configured: skipping migrations (the app will report not-ready until one is connected)"
fi

echo "== building the web app"
(cd frontend && npm run build)
