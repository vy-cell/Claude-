"""Background jobs. Each job runs the same tested `python -m tutorial_editor ...` commands in a
subprocess (so a crash never takes the website down), with its output streamed to a log file the
browser polls. Jobs survive page reloads; a server restart marks interrupted jobs as failed."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import db
from .config import DATA, MAX_PARALLEL_JOBS, PROJECTS, REPO, brand_yaml

JOB_LOGS = DATA / "jobs"
_procs: dict[str, subprocess.Popen] = {}
_cancelled: set[str] = set()


def log_path(job_id: str) -> Path:
    return JOB_LOGS / f"{job_id}.log"


def submit(kind: str, args: dict, user_id: int | None, project_id: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    db.run("INSERT INTO jobs (id, kind, project_id, args, status, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
           job_id, kind, project_id, db.j(args), "queued", user_id, db.now())
    if project_id:
        db.run("UPDATE projects SET status=? WHERE id=?", "queued" if kind == "make" else "working", project_id)
    return job_id


def cancel(job_id: str) -> None:
    _cancelled.add(job_id)
    p = _procs.get(job_id)
    if p and p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)
    db.run("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')", db.now(), job_id)


# ---------------------------------------------------------------- commands per job kind


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "tutorial_editor", "--brand", str(brand_yaml()), *args]


def _info_flags(info: dict) -> list[str]:
    out = []
    for key, flag in (("version", "--version"), ("effective", "--effective"), ("approved_by", "--approved-by"),
                      ("approver_title", "--approver-title"), ("video_url", "--video-url")):
        if info.get(key) not in (None, ""):
            out += [flag, str(info[key])]
    return out


def project_dir(pid: str) -> Path:
    return PROJECTS / pid


def project_output(pid: str) -> Path | None:
    found = sorted((project_dir(pid) / "out").glob("*/outputs.json"))
    return found[0].parent if found else None


def card_pdfs(pid: str) -> dict[str, Path]:
    out = project_output(pid)
    if not out:
        return {}
    return {p.parent.name: p for p in sorted(out.glob("*/*_card_*.pdf")) if not p.name.endswith(".raw.pdf")}


def commands(kind: str, a: dict, pid: str | None) -> list[list[str]]:
    if kind == "make":
        cmd = _cli("make", a["raw"], "--out", str(project_dir(pid) / "out"), "--languages", ",".join(a["languages"]))
        if a.get("notes"):
            cmd += ["--notes", a["notes"]]
        if a.get("voice") == "preview":
            cmd += ["--voice", "preview"]
        if a.get("plan"):
            cmd += ["--plan", a["plan"]]
        return [cmd + _info_flags(a.get("card_info", {}))]
    if kind == "card_regen":
        return [_cli("card", str(project_output(pid)), *_info_flags(a.get("card_info", {})))]
    if kind == "card_fields":  # details only - no AI
        return [_cli("edit-card", str(p), "--out", str(p), *_info_flags(a["card_info"])) for p in card_pdfs(pid).values()]
    if kind == "card_edit":  # each language applies the change to its own card
        return [_cli("edit-card", str(p), "--out", str(p), "--request", a["request"]) for p in card_pdfs(pid).values()]
    if kind == "edit_any":
        cmd = _cli("edit-card", a["pdf"], "--out", a["out"])
        if a.get("request"):
            cmd += ["--request", a["request"]]
        if a.get("languages"):
            cmd += ["--languages", ",".join(a["languages"])]
        return [cmd + _info_flags(a.get("card_info", {}))]
    if kind == "guideline":
        return [_cli("update-guideline", a["pdf"])]
    if kind == "learn_example":
        return [_cli("learn-example", a["video"], "--name", a["name"])]
    if kind == "learn_card":
        return [_cli("learn-card", a["pdf"])]
    if kind == "assets":
        cmd = [sys.executable, str(REPO / "tools" / "extract_brand_assets.py"), "--out", str(Path(brand_yaml()).parent / "assets"),
               "--guideline", a["guideline"]]
        if a.get("sample"):
            cmd += ["--sample", a["sample"]]
        return [cmd]
    raise ValueError(f"unknown job kind {kind}")


# ---------------------------------------------------------------- worker


def _run(job: dict) -> None:
    job_id, pid, kind = job["id"], job["project_id"], job["kind"]
    args = json.loads(job["args"])
    db.run("UPDATE jobs SET status='running', started_at=? WHERE id=?", db.now(), job_id)
    if pid:
        db.run("UPDATE projects SET status='running' WHERE id=?", pid)
    log = log_path(job_id)
    ok, err = True, None
    try:
        with open(log, "a", buffering=1) as f:
            for cmd in commands(kind, args, pid):
                if job_id in _cancelled:
                    break
                f.write(f"$ {' '.join(Path(c).name if c.startswith('/') else c for c in cmd[1:4])} ...\n")
                p = subprocess.Popen(cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
                                     env={**os.environ, "PYTHONUNBUFFERED": "1"})
                _procs[job_id] = p
                code = p.wait()
                if code != 0:
                    ok, err = False, _last_error(log)
                    break
    except Exception as e:  # noqa: BLE001 - surface anything to the user
        ok, err = False, str(e)
    finally:
        _procs.pop(job_id, None)
    status = "cancelled" if job_id in _cancelled else ("done" if ok else "failed")
    result = None
    if pid:
        out = project_output(pid)
        if out and (out / "outputs.json").exists():
            result = json.loads((out / "outputs.json").read_text())
            db.run("UPDATE projects SET outputs=? WHERE id=?", db.j(result), pid)
        db.run("UPDATE projects SET status=? WHERE id=?", "ready" if (ok and result) else ("failed" if not ok else "ready"), pid)
    db.run("UPDATE jobs SET status=?, finished_at=?, error=?, result=? WHERE id=?",
           status, db.now(), err, db.j(result) if result else None, job_id)


FRIENDLY = [
    (("Could not resolve authentication", "invalid x-api-key", "authentication_error"),
     "The Claude AI key isn't set up on the server yet (ANTHROPIC_API_KEY). An admin needs to add it and restart Studio."),
    (("ElevenLabs error 401", "ElevenLabs error 403"), "The voice service rejected its key - check ELEVENLABS_API_KEY and the voice ID."),
    (("credit balance is too low",), "The Claude account is out of credit - top it up in the Claude Console."),
    (("rate_limit", "overloaded_error"), "The AI service is busy right now - please try again in a few minutes."),
    (("No space left on device",), "The server ran out of disk space - delete old videos or add storage."),
]


def _last_error(log: Path) -> str:
    text = log.read_text(errors="replace")
    for needles, msg in FRIENDLY:
        if any(n in text for n in needles):
            return msg
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if any(k in ln for k in ("Error", "error", "Exception", "needs", "not found", "denied")):
            return ln[-500:]
    return (lines[-1] if lines else "failed")[-500:]


def _loop() -> None:
    while True:
        running = len([p for p in _procs.values() if p.poll() is None])
        if running < MAX_PARALLEL_JOBS:
            job = db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if job:
                db.run("UPDATE jobs SET status='starting' WHERE id=?", job["id"])
                threading.Thread(target=_run, args=(job,), daemon=True).start()
                time.sleep(0.2)
                continue
        time.sleep(1.0)


def start_worker() -> None:
    JOB_LOGS.mkdir(parents=True, exist_ok=True)
    db.run("UPDATE jobs SET status='failed', error='interrupted by a server restart' WHERE status IN ('running','starting')")
    db.run("UPDATE projects SET status='failed' WHERE status='running'")
    threading.Thread(target=_loop, daemon=True).start()
