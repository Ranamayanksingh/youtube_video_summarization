"""Thin shim — exposes `app` for uvicorn (uvicorn web_app:app)."""
from app.interfaces.web import app  # noqa: F401

__all__ = ["app"]
