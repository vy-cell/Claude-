"""Summerfield Studio - web server.

    uvicorn webapp.server:app --host 0.0.0.0 --port 8000

Everything the command line does, behind a team login: make videos from raw footage, follow the
director's review live, watch / download every language, fill in or change the SOP card, edit
any card PDF, and keep the brand kit up to date. Installable on phones as an app (PWA).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, jobs
from .auth import COOKIE, current_user, require_admin
from .config import BRAND_DIR, DATA, MAX_UPLOAD_MB, PROJECTS, SECURE_COOKIES, SESSION_DAYS, brand_yaml, init_dirs

STATIC = Path(__file__).parent / "static"
TOOLS = DATA / "tools"
LANG_NAMES = {"en": "English", "vi": "Vietnamese", "es": "Spanish"}

@asynccontextmanager
async def _lifespan(_app):
    init_dirs()
    TOOLS.mkdir(parents=True, exist_ok=True)
    db.init()
    auth.bootstrap_admin()
    jobs.start_worker()
    yield


app = FastAPI(title="Summerfield Studio", docs_url=None, redoc_url=None, lifespan=_lifespan)


@app.middleware("http")
async def _csrf(request: Request, call_next):
    # state-changing API calls must come from our own page (custom header can't be sent cross-site without CORS)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        if request.headers.get("x-studio") != "1":
            return JSONResponse({"detail": "bad request origin"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- helpers


def _safe_name(name: str, default: str = "file") -> str:
    base = Path(name or default).name
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or default
    return base[:120]


async def _save(upload: UploadFile, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with open(dest, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
            f.write(chunk)
    return dest


def _inside(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep) or not p.is_file():
        raise HTTPException(404, "Not found")
    return p


def _card_info_from_form(version, effective, approved_by, approver_title, video_url) -> dict:
    return {k: v.strip() for k, v in dict(version=version, effective=effective, approved_by=approved_by,
                                          approver_title=approver_title, video_url=video_url).items() if v and v.strip()}


def _notes_from_upload(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout
    return path.read_text(errors="replace")


def _file_url(pid: str, p: str | Path) -> str:
    rel = Path(p).resolve().relative_to((PROJECTS / pid).resolve())
    return f"/files/projects/{pid}/{rel.as_posix()}"


def _project_view(p: dict, full: bool = False) -> dict:
    pid = p["id"]
    outputs = json.loads(p["outputs"] or "{}")
    langs = json.loads(p["languages"])
    out_dir = jobs.project_output(pid)
    thumb = None
    if out_dir:
        t = out_dir / "work" / "card_scenes" / "scene_000.jpg"
        if not t.exists():
            frames = sorted((out_dir / "work" / "review").glob("*/scene_000.jpg"))
            t = frames[-1] if frames else t
        if t.exists():
            thumb = _file_url(pid, t)
    job = db.one("SELECT id, kind, status, error, created_at FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", pid)
    view = {"id": pid, "title": p["title"], "status": p["status"], "created_at": p["created_at"], "languages": langs,
            "thumb": thumb, "latest_job": job, "card_info": json.loads(p["card_info"] or "{}")}
    if not full:
        return view
    results = []
    for code in langs:
        o = outputs.get(code, {})
        entry = {"code": code, "name": LANG_NAMES.get(code, code), "seconds": o.get("seconds")}
        for key in ("video", "srt", "card"):
            if o.get(key) and Path(o[key]).exists():
                entry[key] = _file_url(pid, o[key])
        if o.get("card"):
            pages = sorted(Path(o["card"]).parent.glob(Path(o["card"]).stem + "-page-*.png"))
            entry["card_pages"] = [_file_url(pid, x) for x in pages]
        results.append(entry)
    view["results"] = results
    view["review_log"] = (out_dir / "review_log.md").read_text() if out_dir and (out_dir / "review_log.md").exists() else ""
    plan = out_dir / "en" / "plan_en.json" if out_dir else None
    view["open_questions"] = json.loads(plan.read_text()).get("open_questions", []) if plan and plan.exists() else []
    view["jobs"] = db.all_("SELECT id, kind, status, error, created_at, finished_at FROM jobs WHERE project_id=? ORDER BY created_at DESC", pid)
    view["has_plan"] = bool(plan and plan.exists())
    return view


def _project_or_404(pid: str) -> dict:
    p = db.one("SELECT * FROM projects WHERE id=?", pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


# ---------------------------------------------------------------- auth


class LoginIn(BaseModel):
    email: str
    password: str


class SetupIn(BaseModel):
    email: str
    name: str
    password: str


def _set_session(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=SECURE_COOKIES)


@app.get("/api/setup-status")
def setup_status():
    return {"needs_admin": db.one("SELECT id FROM users LIMIT 1") is None}


@app.post("/api/setup")
def setup(body: SetupIn, resp: Response):
    if db.one("SELECT id FROM users LIMIT 1"):
        raise HTTPException(409, "Already set up")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    auth.create_user(body.email, body.name, body.password, "admin")
    _set_session(resp, auth.login(body.email, body.password))
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginIn, resp: Response):
    token = auth.login(body.email, body.password)
    if not token:
        raise HTTPException(401, "Wrong email or password")
    _set_session(resp, token)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    if request.cookies.get(COOKIE):
        auth.logout(request.cookies[COOKIE])
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


class PasswordIn(BaseModel):
    password: str


@app.post("/api/me/password")
def change_my_password(body: PasswordIn, user: dict = Depends(current_user)):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    db.run("UPDATE users SET pw_hash=? WHERE id=?", auth.hash_pw(body.password), user["id"])
    return {"ok": True}


class UserIn(BaseModel):
    email: str
    name: str
    password: str
    role: str = "editor"


@app.get("/api/users")
def list_users(_: dict = Depends(require_admin)):
    return db.all_("SELECT id, email, name, role, created_at FROM users ORDER BY created_at")


@app.post("/api/users")
def add_user(body: UserIn, _: dict = Depends(require_admin)):
    if body.role not in ("admin", "editor"):
        raise HTTPException(400, "Role must be admin or editor")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.one("SELECT id FROM users WHERE email=?", body.email.strip().lower()):
        raise HTTPException(409, "That email already has an account")
    return auth.create_user(body.email, body.name, body.password, body.role)


@app.delete("/api/users/{uid}")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "You can't remove yourself")
    db.run("DELETE FROM sessions WHERE user_id=?", uid)
    db.run("DELETE FROM users WHERE id=?", uid)
    return {"ok": True}


# ---------------------------------------------------------------- projects


@app.get("/api/projects")
def list_projects(_: dict = Depends(current_user)):
    return [_project_view(p) for p in db.all_("SELECT * FROM projects ORDER BY created_at DESC")]


@app.post("/api/projects")
async def create_project(
    title: str = Form(...), raw: UploadFile = File(...), notes_text: str = Form(""), notes_file: UploadFile | None = File(None),
    languages: str = Form("en,vi,es"), voice: str = Form("real"),
    version: str = Form(""), effective: str = Form(""), approved_by: str = Form(""), approver_title: str = Form(""),
    video_url: str = Form(""), user: dict = Depends(current_user),
):
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise HTTPException(503, "The Claude AI key isn't set up on the server yet - ask an admin to add ANTHROPIC_API_KEY.")
    pid = uuid.uuid4().hex[:10]
    pdir = PROJECTS / pid
    raw_path = await _save(raw, pdir / "raw" / _safe_name(raw.filename, "footage.mp4"))
    notes = notes_text.strip()
    if notes_file and notes_file.filename:
        nf = await _save(notes_file, pdir / "notes" / _safe_name(notes_file.filename, "notes.txt"))
        notes = (notes + "\n\n" + _notes_from_upload(nf)).strip()
    notes_path = None
    if notes:
        notes_path = pdir / "notes.md"
        notes_path.write_text(notes)
    langs = [c for c in (x.strip() for x in languages.split(",")) if c in LANG_NAMES] or ["en"]
    if "en" not in langs:
        langs.insert(0, "en")
    info = _card_info_from_form(version, effective, approved_by, approver_title, video_url)
    db.run("INSERT INTO projects (id, title, created_by, created_at, raw_name, languages, card_info) VALUES (?,?,?,?,?,?,?)",
           pid, title.strip() or raw_path.stem, user["id"], db.now(), raw_path.name, db.j(langs), db.j(info))
    job_id = jobs.submit("make", {"raw": str(raw_path), "notes": str(notes_path) if notes_path else "", "languages": langs,
                                  "voice": "preview" if voice == "preview" else "real", "card_info": info}, user["id"], pid)
    return {"id": pid, "job_id": job_id}


@app.get("/api/projects/{pid}")
def get_project(pid: str, _: dict = Depends(current_user)):
    return _project_view(_project_or_404(pid), full=True)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, user: dict = Depends(current_user)):
    p = _project_or_404(pid)
    if user["role"] != "admin" and p["created_by"] != user["id"]:
        raise HTTPException(403, "Only the creator or an admin can delete this")
    for j in db.all_("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running','starting')", pid):
        jobs.cancel(j["id"])
    shutil.rmtree(PROJECTS / pid, ignore_errors=True)
    db.run("DELETE FROM jobs WHERE project_id=?", pid)
    db.run("DELETE FROM projects WHERE id=?", pid)
    return {"ok": True}


class RemakeIn(BaseModel):
    voice: str = "real"
    keep_plan: bool = True


@app.post("/api/projects/{pid}/remake")
def remake(pid: str, body: RemakeIn, user: dict = Depends(current_user)):
    p = _project_or_404(pid)
    pdir = PROJECTS / pid
    raw = pdir / "raw" / p["raw_name"]
    notes = pdir / "notes.md"
    args = {"raw": str(raw), "notes": str(notes) if notes.exists() else "", "languages": json.loads(p["languages"]),
            "voice": body.voice, "card_info": json.loads(p["card_info"] or "{}")}
    out = jobs.project_output(pid)
    if body.keep_plan and out and (out / "en" / "plan_en.json").exists():
        keep = pdir / "plan_keep.json"
        shutil.copy2(out / "en" / "plan_en.json", keep)
        args["plan"] = str(keep)
    return {"job_id": jobs.submit("make", args, user["id"], pid)}


class CardInfoIn(BaseModel):
    version: str = ""
    effective: str = ""
    approved_by: str = ""
    approver_title: str = ""
    video_url: str = ""


@app.post("/api/projects/{pid}/card/fields")
def card_fields(pid: str, body: CardInfoIn, user: dict = Depends(current_user)):
    _project_or_404(pid)
    if not jobs.card_pdfs(pid):
        raise HTTPException(409, "This project has no cards yet")
    info = {k: v.strip() for k, v in body.model_dump().items() if v and v.strip()}
    db.run("UPDATE projects SET card_info=? WHERE id=?", db.j(info), pid)
    return {"job_id": jobs.submit("card_fields", {"card_info": info}, user["id"], pid)}


@app.post("/api/projects/{pid}/card/regenerate")
def card_regenerate(pid: str, body: CardInfoIn, user: dict = Depends(current_user)):
    _project_or_404(pid)
    if not jobs.project_output(pid):
        raise HTTPException(409, "Make the video first")
    info = {k: v.strip() for k, v in body.model_dump().items() if v and v.strip()}
    db.run("UPDATE projects SET card_info=? WHERE id=?", db.j(info), pid)
    return {"job_id": jobs.submit("card_regen", {"card_info": info}, user["id"], pid)}


class EditIn(BaseModel):
    request: str


@app.post("/api/projects/{pid}/card/edit")
def card_edit(pid: str, body: EditIn, user: dict = Depends(current_user)):
    _project_or_404(pid)
    if not body.request.strip():
        raise HTTPException(400, "Say what to change")
    if not jobs.card_pdfs(pid):
        raise HTTPException(409, "This project has no cards yet")
    return {"job_id": jobs.submit("card_edit", {"request": body.request.strip()}, user["id"], pid)}


# ---------------------------------------------------------------- jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, since: int = 0, _: dict = Depends(current_user)):
    j = db.one("SELECT * FROM jobs WHERE id=?", job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    log = jobs.log_path(job_id)
    text, size = "", 0
    if log.exists():
        size = log.stat().st_size
        with open(log, "rb") as f:
            f.seek(max(since, size - 200_000) if since < size else size)
            text = f.read().decode(errors="replace")
    j["args"] = json.loads(j["args"])
    j["result"] = json.loads(j["result"]) if j["result"] else None
    return {"job": j, "log": text, "offset": size}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, _: dict = Depends(current_user)):
    jobs.cancel(job_id)
    return {"ok": True}


# ---------------------------------------------------------------- edit any card


@app.post("/api/tools/edit-card")
async def edit_any_card(
    pdf: UploadFile = File(...), request: str = Form(""), languages: str = Form(""),
    version: str = Form(""), effective: str = Form(""), approved_by: str = Form(""), approver_title: str = Form(""),
    video_url: str = Form(""), user: dict = Depends(current_user),
):
    tid = uuid.uuid4().hex[:10]
    d = TOOLS / tid
    src = await _save(pdf, d / "input" / _safe_name(pdf.filename, "card.pdf"))
    info = _card_info_from_form(version, effective, approved_by, approver_title, video_url)
    if not request.strip() and not info:
        raise HTTPException(400, "Add a change request or at least one field to change")
    stem = re.sub(r"(_edited)+$", "", src.stem)
    out = d / f"{stem}_edited.pdf"
    langs = [c for c in (x.strip() for x in languages.split(",")) if c in LANG_NAMES]
    job_id = jobs.submit("edit_any", {"pdf": str(src), "out": str(out), "request": request.strip(), "languages": langs,
                                      "card_info": info, "tool_id": tid}, user["id"])
    return {"job_id": job_id, "tool_id": tid}


@app.get("/api/tools/edit-card/{tid}")
def edit_any_result(tid: str, _: dict = Depends(current_user)):
    d = TOOLS / tid
    if not d.exists():
        raise HTTPException(404, "Not found")
    pdfs = [p for p in sorted(d.glob("*.pdf")) if not p.name.endswith(".raw.pdf")]
    return {"files": [{"name": p.name, "url": f"/files/tools/{tid}/{p.name}",
                       "pages": [f"/files/tools/{tid}/{x.name}" for x in sorted(d.glob(p.stem + "-page-*.png"))]} for p in pdfs]}


# ---------------------------------------------------------------- brand kit


def _brand():
    from tutorial_editor.brand import load_brand

    return load_brand(brand_yaml())


@app.get("/api/brand")
def brand_info(_: dict = Depends(current_user)):
    b = _brand()
    assets = {}
    for key in ("logo_horizontal", "logo_mark", "mascot"):
        p = b.path(getattr(b.assets, key))
        assets[key] = f"/files/brand/{p.relative_to(BRAND_DIR).as_posix()}?v={int(p.stat().st_mtime)}" if p and p.exists() else None
    examples = sorted(BRAND_DIR.glob("examples/*/ref_*.jpg"))
    card_ref = sorted(BRAND_DIR.glob("examples/card/page-*.png"))
    return {
        "name": b.name, "colors": b.colors.model_dump(), "fonts": b.fonts.model_dump(), "card_info": b.card_info.model_dump(),
        "voice": {"provider": b.voiceover.provider, "voice_id": b.voiceover.voice_id, "model_id": b.voiceover.model_id},
        "template": b.template.model_dump(), "assets": assets,
        "example_frames": [f"/files/brand/{p.relative_to(BRAND_DIR).as_posix()}" for p in examples[:12]],
        "card_reference": [f"/files/brand/{p.relative_to(BRAND_DIR).as_posix()}" for p in card_ref],
        "keys": {"claude": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                 "voice": bool(os.environ.get("ELEVENLABS_API_KEY"))},
        "style_guide": b.style_guide_text(),
        "jobs": db.all_("SELECT id, kind, status, error, created_at FROM jobs WHERE project_id IS NULL AND kind != 'edit_any' "
                        "ORDER BY created_at DESC LIMIT 10"),
    }


class BrandSettingsIn(BaseModel):
    voice_id: str | None = None
    approved_by: str | None = None
    approver_title: str | None = None
    version: str | None = None
    video_url: str | None = None
    series_label: str | None = None
    badge_text: str | None = None


def set_yaml_field(path: Path, key: str, value: str) -> None:
    """Replace one scalar `key: value` line in place, keeping indentation and comments."""
    text = path.read_text()
    val = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    new, n = re.subn(rf'^(\s*{re.escape(key)}:\s*)("(?:[^"\\]|\\.)*"|[^#\n]*?)(\s*(#.*)?)$',
                     lambda m: f"{m.group(1)}{val}{m.group(3)}", text, count=1, flags=re.M)
    if not n:
        raise HTTPException(500, f"setting {key} not found in brand.yaml")
    path.write_text(new)


@app.post("/api/brand/settings")
def brand_settings(body: BrandSettingsIn, _: dict = Depends(require_admin)):
    for k, v in body.model_dump().items():
        if v is not None:
            set_yaml_field(brand_yaml(), k, v.strip())
    _brand()  # validate
    return {"ok": True}


async def _brand_upload(f: UploadFile, sub: str) -> Path:
    return await _save(f, DATA / "brand_uploads" / uuid.uuid4().hex[:8] / _safe_name(f.filename, sub))


@app.post("/api/brand/guideline")
async def brand_guideline(pdf: UploadFile = File(...), user: dict = Depends(require_admin)):
    p = await _brand_upload(pdf, "guideline.pdf")
    return {"job_id": jobs.submit("guideline", {"pdf": str(p)}, user["id"])}


@app.post("/api/brand/example")
async def brand_example(video: UploadFile = File(...), name: str = Form(...), user: dict = Depends(require_admin)):
    p = await _brand_upload(video, "example.mp4")
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "example"
    return {"job_id": jobs.submit("learn_example", {"video": str(p), "name": slug}, user["id"])}


@app.post("/api/brand/card-reference")
async def brand_card_reference(pdf: UploadFile = File(...), user: dict = Depends(require_admin)):
    p = await _brand_upload(pdf, "card.pdf")
    return {"job_id": jobs.submit("learn_card", {"pdf": str(p)}, user["id"])}


@app.post("/api/brand/extract-assets")
async def brand_extract(guideline: UploadFile = File(...), sample: UploadFile | None = File(None), user: dict = Depends(require_admin)):
    g = await _brand_upload(guideline, "guideline.pdf")
    args = {"guideline": str(g)}
    if sample and sample.filename:
        args["sample"] = str(await _brand_upload(sample, "sample.mp4"))
    return {"job_id": jobs.submit("assets", args, user["id"])}


ASSET_FILES = {"logo_horizontal": "assets/logo-horizontal-dark.png", "logo_mark": "assets/logomark-dark.png", "mascot": "assets/mascot.png"}


@app.post("/api/brand/asset/{kind}")
async def brand_asset(kind: str, file: UploadFile = File(...), _: dict = Depends(require_admin)):
    if kind not in ASSET_FILES:
        raise HTTPException(404, "Unknown asset")
    tmp = await _brand_upload(file, "asset.png")
    from PIL import Image

    try:
        im = Image.open(tmp).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "Please upload a PNG image") from e
    dest = BRAND_DIR / ASSET_FILES[kind]
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    return {"ok": True}


# ---------------------------------------------------------------- files


@app.get("/files/projects/{pid}/{rel:path}")
def project_file(pid: str, rel: str, _: dict = Depends(current_user)):
    return FileResponse(_inside(PROJECTS / pid, rel))


@app.get("/files/tools/{tid}/{rel:path}")
def tool_file(tid: str, rel: str, _: dict = Depends(current_user)):
    return FileResponse(_inside(TOOLS / tid, rel))


@app.get("/files/brand/{rel:path}")
def brand_file(rel: str, _: dict = Depends(current_user)):
    return FileResponse(_inside(BRAND_DIR, rel))


# ---------------------------------------------------------------- app shell (PWA)


@app.get("/icon-{size}.png")
def icon(size: int):
    from PIL import Image, ImageDraw

    size = 512 if size > 256 else 192
    cache = DATA / f"icon-{size}.png"
    mark = BRAND_DIR / "assets" / "logomark-dark.png"
    if not cache.exists() or (mark.exists() and mark.stat().st_mtime > cache.stat().st_mtime):
        im = Image.new("RGB", (size, size), (227, 232, 227))
        if mark.exists():
            m = Image.open(mark).convert("RGBA")
            m.thumbnail((int(size * 0.56), int(size * 0.56)))
            im.paste(m, ((size - m.width) // 2, (size - m.height) // 2), m)
        else:
            d = ImageDraw.Draw(im)
            d.regular_polygon((size / 2, size / 2, size * 0.3), 8, rotation=22.5, outline=(35, 31, 32), width=max(size // 40, 3))
        im.save(cache)
    return FileResponse(cache, media_type="image/png")


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Summerfield Studio", "short_name": "Studio", "start_url": "/", "display": "standalone",
        "background_color": "#E3E8E3", "theme_color": "#E3E8E3",
        "description": "On-brand training videos and SOP cards for Summerfield Tea Bar",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/fonts", StaticFiles(directory=Path(__file__).resolve().parent.parent / "brand" / "fonts"), name="fonts")


@app.get("/{path:path}", response_class=HTMLResponse)
def shell(path: str):
    if path.startswith(("api/", "files/")):
        raise HTTPException(404, "Not found")
    return HTMLResponse((STATIC / "index.html").read_text(), headers={"Cache-Control": "no-cache"})
