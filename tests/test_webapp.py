"""Web app: accounts, permissions, request-forgery guard, file safety, and a real background job
(editing a card's details, which needs no AI) running end to end through the API."""

import importlib
import time

import pytest
from fastapi.testclient import TestClient

H = {"x-studio": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch, brand, raw_video):
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import webapp.config
    import webapp.db
    import webapp.jobs
    import webapp.server

    for m in (webapp.config, webapp.db, webapp.jobs, webapp.server):
        importlib.reload(m)
    with TestClient(webapp.server.app) as c:
        yield c


def _setup(c):
    r = c.post("/api/setup", json={"email": "Admin@Example.com", "name": "Ada", "password": "longpassword"}, headers=H)
    assert r.status_code == 200


def test_first_run_setup_login_and_roles(client):
    assert client.get("/api/setup-status").json() == {"needs_admin": True}
    _setup(client)
    assert client.get("/api/me").json()["role"] == "admin"
    assert client.post("/api/setup", json={"email": "x@y.z", "name": "X", "password": "longpassword"}, headers=H).status_code == 409
    r = client.post("/api/users", json={"email": "ed@example.com", "name": "Ed", "password": "editorpass"}, headers=H)
    assert r.status_code == 200
    client.post("/api/logout", headers=H)
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"email": "ed@example.com", "password": "wrong"}, headers=H).status_code == 401
    assert client.post("/api/login", json={"email": "ed@example.com", "password": "editorpass"}, headers=H).status_code == 200
    assert client.get("/api/users").status_code == 403  # editors can't manage the team
    assert client.post("/api/brand/settings", json={"voice_id": "x"}, headers=H).status_code == 403


def test_forgery_guard_and_file_safety(client):
    _setup(client)
    assert client.post("/api/logout").status_code == 403  # no custom header -> refused
    r = client.get("/files/brand/../../studio.db")  # client normalises to /studio.db -> just the app page
    assert b"SQLite" not in r.content
    assert client.get("/files/brand/%2e%2e/%2e%2e/studio.db").status_code == 404
    assert client.get("/files/brand/..%2f..%2fstudio.db").status_code == 404
    assert client.get("/files/brand/brand.yaml").status_code == 200
    client.cookies.clear()
    assert client.get("/files/brand/brand.yaml").status_code == 401


def test_making_a_video_needs_the_ai_key(client, raw_video):
    _setup(client)
    with open(raw_video, "rb") as f:
        r = client.post("/api/projects", data={"title": "t"}, files={"raw": ("raw.mp4", f, "video/mp4")}, headers=H)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_brand_settings_edit_yaml_in_place(client):
    _setup(client)
    assert client.post("/api/brand/settings", json={"voice_id": "abc123", "approved_by": 'Karina "K" Fong'}, headers=H).status_code == 200
    b = client.get("/api/brand").json()
    assert b["voice"]["voice_id"] == "abc123" and b["card_info"]["approved_by"] == 'Karina "K" Fong'
    assert b["keys"] == {"claude": False, "voice": False}


def test_edit_card_details_job_runs_end_to_end(client, brand, raw_video, tmp_path):
    from tests.fixtures.make_card import card
    from tutorial_editor.brand import CardInfo
    from tutorial_editor.card import PhotoSources, render_card, resolve_photos

    c = card()
    resolve_photos(c, PhotoSources(tmp_path / "ph", raw_video))
    pdf = render_card(brand, c, CardInfo(version="1.0", effective="10/16/2026"), "en", tmp_path / "ph", tmp_path / "card_en.pdf").pdf

    _setup(client)
    with open(pdf, "rb") as f:
        r = client.post("/api/tools/edit-card", data={"approved_by": "Jamie Tran", "version": "2.0"},
                        files={"pdf": ("card_en.pdf", f, "application/pdf")}, headers=H)
    assert r.status_code == 200, r.text
    job_id, tool_id = r.json()["job_id"], r.json()["tool_id"]
    for _ in range(90):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["job"]["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert j["job"]["status"] == "done", j["log"]
    files = client.get(f"/api/tools/edit-card/{tool_id}").json()["files"]
    assert files and files[0]["pages"]
    got = client.get(files[0]["url"])
    assert got.status_code == 200 and got.content[:4] == b"%PDF"
