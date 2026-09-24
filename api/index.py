"""Vercel entry point: the FastAPI application (backend/app) served as one serverless function.

Vercel routes /api/* and /health/* here (see vercel.json); the app sees the original path. Everything else is the static React build.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.hosting import apply_vercel_defaults  # noqa: E402

apply_vercel_defaults(os.environ)  # Vercel-only defaults; nothing happens elsewhere

from app.main import app  # noqa: E402,F401  (the ASGI application Vercel looks for)
