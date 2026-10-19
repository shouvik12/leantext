"""Content-addressed cache so repeat reads cost nothing. Disable with --no-cache or LEANTEXT_NO_CACHE=1."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DIR = Path(os.environ.get("LEANTEXT_CACHE_DIR", Path.home() / ".cache" / "leantext"))


def key(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode())
        h.update(b"\0")
    return h.hexdigest()


def enabled() -> bool:
    return os.environ.get("LEANTEXT_NO_CACHE", "") in ("", "0")


def get(k: str) -> dict | None:
    if not enabled():
        return None
    f = DIR / f"{k}.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def put(k: str, value: dict) -> None:
    if not enabled():
        return
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / f"{k}.json").write_text(json.dumps(value))
    except OSError:
        pass
