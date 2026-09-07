"""
Runtime configuration for the DOM broker.

Zero-dependency: reads an optional `.env` at the repo root, then falls back to
process environment variables, then to sane local defaults. Uvicorn CLI flags
(`--host` / `--port`) always win over these, which is what you want on Render.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Do not clobber values already present in the real environment.
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Render injects PORT; honour it first so the same code deploys unchanged.
HOST: str = os.getenv("DOM_HOST", "127.0.0.1")
PORT: int = int(os.getenv("PORT") or os.getenv("DOM_PORT") or "8000")

# Broadcast cadence (ms). 300ms feels live while keeping flashes readable.
TICK_MS: int = int(os.getenv("DOM_TICK_MS", "300"))

# Price levels per side.
DEPTH: int = int(os.getenv("DOM_DEPTH", "10"))

# Comma-separated allowed origins, or "*" for any (fine for a public read-only feed).
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("DOM_CORS_ORIGINS", "*").split(",") if o.strip()
] or ["*"]

WS_PATH: str = os.getenv("DOM_WS_PATH", "/dom")
