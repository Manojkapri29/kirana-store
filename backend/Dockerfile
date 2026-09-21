# Backend image: FastAPI served by uvicorn. No secret is baked in: every setting comes from the environment at run time
# (see docs/PRODUCTION_DEPLOYMENT.md). Build:  docker build -t kirana-backend ./backend
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/backend

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# A user with no login shell and no write access to the code. Data lives on the /data volume only.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin kirana \
    && mkdir -p /data /data/backups \
    && chown -R kirana /data \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

USER 10001
ENV KIRANA_ENVIRONMENT=production \
    KIRANA_DATABASE_URL=sqlite:////data/kirana.db \
    KIRANA_BACKUP_DIR=/data/backups
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
