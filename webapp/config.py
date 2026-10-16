"""Paths and settings, all from environment variables (see .env.example)."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STUDIO_DATA_DIR", REPO / "data")).resolve()
BRAND_DIR = DATA / "brand"  # working copy of brand/ - guideline updates and uploaded assets land here
PROJECTS = DATA / "projects"
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "studio.db"
MAX_PARALLEL_JOBS = int(os.environ.get("STUDIO_MAX_PARALLEL_JOBS", "1"))
MAX_UPLOAD_MB = int(os.environ.get("STUDIO_MAX_UPLOAD_MB", "4000"))
SESSION_DAYS = int(os.environ.get("STUDIO_SESSION_DAYS", "30"))
SECURE_COOKIES = os.environ.get("STUDIO_SECURE_COOKIES", "0") == "1"  # set to 1 behind HTTPS


def secret_key() -> bytes:
    env = os.environ.get("STUDIO_SECRET_KEY")
    if env:
        return env.encode()
    f = DATA / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip().encode()


def init_dirs() -> None:
    for d in (DATA, PROJECTS, UPLOADS):
        d.mkdir(parents=True, exist_ok=True)
    if not (BRAND_DIR / "brand.yaml").exists():
        shutil.copytree(REPO / "brand", BRAND_DIR, dirs_exist_ok=True)


def brand_yaml() -> Path:
    return BRAND_DIR / "brand.yaml"
