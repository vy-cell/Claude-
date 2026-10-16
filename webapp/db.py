"""SQLite storage: users, sessions, projects, jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_by INTEGER, created_at REAL NOT NULL,
    raw_name TEXT, languages TEXT NOT NULL DEFAULT '["en","vi","es"]', card_info TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'new', outputs TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT, args TEXT NOT NULL, status TEXT NOT NULL,
    created_by INTEGER, created_at REAL NOT NULL, started_at REAL, finished_at REAL, error TEXT, result TEXT);
"""


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_conn: sqlite3.Connection | None = None


def init() -> None:
    global _conn
    _conn = connect()
    _conn.executescript(SCHEMA)
    _conn.commit()


@contextmanager
def tx():
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def one(sql: str, *args) -> dict | None:
    with tx() as c:
        r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def all_(sql: str, *args) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def run(sql: str, *args) -> None:
    with tx() as c:
        c.execute(sql, args)


def now() -> float:
    return time.time()


def j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
