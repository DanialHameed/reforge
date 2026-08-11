"""
Compatibility shim for older Docker/commands.

Local-dev canonical entrypoint is:
  uvicorn app.main:app --reload
"""

from app.main import app  # noqa: F401

