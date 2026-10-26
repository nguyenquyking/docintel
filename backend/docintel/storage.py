"""Content-addressed blob store and per-document artifacts.

Blobs live at ``data/blobs/ab/cd/<sha256>.<ext>``: the same bytes uploaded twice are
stored once, and the hash doubles as the idempotency key for processing. Parsed
artifacts (the page IR, rendered page images) are keyed by blob hash *and* parser
version, so re-running extraction never re-runs OCR, and a parser change
re-parses exactly the documents it affects.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .settings import Settings, get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Storage:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.root = Path(self.settings.data_dir)
        self.blobs = self.root / "blobs"
        self.artifacts = self.root / "artifacts"
        self.models = self.root / "models"
        for d in (self.blobs, self.artifacts, self.models):
            d.mkdir(parents=True, exist_ok=True)

    # ── blobs ────────────────────────────────────────────────────────────
    def blob_path(self, sha: str, ext: str = ".pdf") -> Path:
        return self.blobs / sha[:2] / sha[2:4] / f"{sha}{ext}"

    def put_blob(self, data: bytes, ext: str = ".pdf") -> tuple[str, Path, bool]:
        """Returns (sha256, path, created). Written atomically: a crash never leaves half a file."""
        sha = sha256_bytes(data)
        path = self.blob_path(sha, ext)
        if path.exists():
            return sha, path, False
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return sha, path, True

    # ── artifacts ────────────────────────────────────────────────────────
    def artifact_dir(self, sha: str, version: str) -> Path:
        d = self.artifacts / sha / version
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_json(self, path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def read_json(self, path: Path) -> Any | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
