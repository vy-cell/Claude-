"""Team accounts: scrypt password hashes, server-side session tokens in an HttpOnly cookie."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Depends, HTTPException, Request

from . import db
from .config import SESSION_DAYS

COOKIE = "studio_session"


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except ValueError:
        return False


def create_user(email: str, name: str, pw: str, role: str = "editor") -> dict:
    db.run("INSERT INTO users (email, name, pw_hash, role, created_at) VALUES (?,?,?,?,?)",
           email.strip().lower(), name.strip(), hash_pw(pw), role, db.now())
    return db.one("SELECT id, email, name, role FROM users WHERE email=?", email.strip().lower())


def bootstrap_admin() -> None:
    """First start: create the admin from STUDIO_ADMIN_EMAIL / STUDIO_ADMIN_PASSWORD."""
    if db.one("SELECT id FROM users LIMIT 1"):
        return
    email, pw = os.environ.get("STUDIO_ADMIN_EMAIL"), os.environ.get("STUDIO_ADMIN_PASSWORD")
    if email and pw:
        create_user(email, os.environ.get("STUDIO_ADMIN_NAME", "Admin"), pw, "admin")


def login(email: str, pw: str) -> str | None:
    u = db.one("SELECT * FROM users WHERE email=?", email.strip().lower())
    if not u or not check_pw(pw, u["pw_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions (token, user_id, expires) VALUES (?,?,?)", token, u["id"], db.now() + SESSION_DAYS * 86400)
    return token


def logout(token: str) -> None:
    db.run("DELETE FROM sessions WHERE token=?", token)


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        s = db.one("SELECT user_id, expires FROM sessions WHERE token=?", token)
        if s and s["expires"] > db.now():
            u = db.one("SELECT id, email, name, role FROM users WHERE id=?", s["user_id"])
            if u:
                return u
    raise HTTPException(401, "Please sign in")


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return user
