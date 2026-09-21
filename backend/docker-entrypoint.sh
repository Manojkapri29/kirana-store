#!/bin/sh
# Optionally apply migrations before starting, then hand over to the command (uvicorn or the worker).
# Set KIRANA_RUN_MIGRATIONS=true on the ONE service that should migrate (the web service), never on the worker.
set -eu
if [ "${KIRANA_RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi
exec "$@"
