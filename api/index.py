"""Vercel serverless entrypoint (ASGI).

Vercel's Python runtime serves the module-level `app` (FastAPI is ASGI). We add
the repo root and `src/` to sys.path because there's no editable install in the
serverless bundle; the spine/Kalshi data files are shipped via `includeFiles` in
vercel.json and resolve through the package's existing relative paths.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from webapp.app import app  # noqa: E402  (path setup must precede import)

__all__ = ["app"]
