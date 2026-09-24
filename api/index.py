"""Vercel entry point: the FastAPI application (backend/app) served as one serverless function.

Vercel routes /api/* and /health/* here (see vercel.json); the app sees the original path. Everything else is the static React build.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402,F401  (the ASGI application Vercel looks for)
