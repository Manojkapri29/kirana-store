#!/usr/bin/env bash
# Vercel build: (1) bring the database to the current schema when one is configured, (2) build the React app.
# The database URL comes from the environment (DATABASE_URL / POSTGRES_URL from the Neon integration, or KIRANA_DATABASE_URL); it is never in Git.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv /tmp/kirana-venv
/tmp/kirana-venv/bin/pip install --quiet -r backend/requirements.txt
# Some integrations name the URL differently (for example STORAGE_URL): find it. Only variable NAMES are ever printed.
eval "$(cd backend && /tmp/kirana-venv/bin/python -m app.core.hosting)"
echo "== database variable present: $([ -n "${KIRANA_DATABASE_URL:-}${DATABASE_URL:-}${POSTGRES_URL:-}" ] && echo yes || echo no)"
echo "== variables that look database-related (names only): $(env | cut -d= -f1 | grep -iE 'url|postgres|pg|neon|database|storage' | tr '\n' ' ')"

if [ -n "${KIRANA_DATABASE_URL:-}${DATABASE_URL:-}${POSTGRES_URL:-}" ]; then
  echo "== migrating the database"
  (cd backend && /tmp/kirana-venv/bin/python -m alembic upgrade head)
  # First deployment only: if the database has no account yet, create the first shop and its owner. The owner's email is derived from this
  # site's address (owner@<address>) so no personal data is committed; the ONE-TIME PASSWORD is printed below, in this build log only.
  if ! (cd backend && /tmp/kirana-venv/bin/python -m app.account_cli list) | grep -q "@"; then
    echo "== creating the first shop and owner (only because the database has none)"
    (cd backend && /tmp/kirana-venv/bin/python -m app.account_cli create-shop \
      --shop-name "My Shop" --owner-email "owner@${VERCEL_PROJECT_PRODUCTION_URL:-shop.example.com}" --owner-name "Shop Owner")
  fi
else
  echo "== no database configured: skipping migrations (the app will report not-ready until one is connected)"
fi

echo "== building the web app"
(cd frontend && npm run build)
