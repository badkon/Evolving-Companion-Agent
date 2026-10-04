"""Console uses synthetic config/DB and fake runtime; never calls provider APIs."""

from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sqlite3

import pytest
from starlette.testclient import TestClient

from evolving_companion.config_env import OperationResult
from evolving_companion.console_services import ConsoleService
from evolving_companion.manager_services import ManagerService, ManagerStatus
from evolving_companion.storage import SCHEMA
from evolving_companion.web_setup import ASSETS, PAGES, create_app
from evolving_companion.web_setup_services import WebSetupService


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    for name in ("config/si.env.example", "data/characters/si_001.yaml"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / name, target)
    manager = ManagerService(
        tmp_path / "config/si.env", project_root=tmp_path, environment={}
    )
    status = ManagerStatus(
        "玲 / SI-001",
        "uuid",
        "Stopped",
        "Incomplete",
        "Missing",
        "none",
        "Error (local only)",
    )
    monkeypatch.setattr(manager, "status", lambda: status)
    service = WebSetupService(manager.env_file, tmp_path, {}, manager=manager)
    return service, manager


def web_client(service):
    return TestClient(
        create_app(service, "fake-console-token"),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer fake-console-token"},
    )


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.assets = []
        self.ids = []
        self.avatars = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if "id" in data:
            self.ids.append(data["id"])
        if tag == "a" and data.get("href", "").startswith("/"):
            self.links.append(data["href"])
        if tag in {"link", "script"}:
            self.assets.append(data.get("href") or data.get("src"))
        if data.get("class") == "avatar":
            self.avatars += 1


@pytest.mark.parametrize("path", PAGES)
def test_pages_auth_navigation_assets_and_responsive_markup(setup, path):
    service, _ = setup
    with web_client(service) as web:
        response = web.get(path)
        assert response.status_code == 200
        assert f"<h1>{PAGES[path]}</h1>" in response.text
        assert 'name="viewport"' in response.text
        assert f'href="{path}" aria-current="page"' in response.text
        markup = Markup(response.text)
        assert markup.avatars == 1
        assert len(markup.ids) == len(set(markup.ids))
        assert set(markup.links) == set(PAGES)
        for asset in markup.assets:
            assert web.get(asset).status_code == 200
        unauth = web.get(path, headers={"Authorization": "invalid"})
        assert unauth.status_code == 401
        assert 'data-login="true"' in unauth.text
        assert "<h1>" + PAGES[path] not in unauth.text
        assert "fake-console-token" not in response.text
        assert web.get("/static/base.html").status_code == 404
    css = (ASSETS / "setup.css").read_text(encoding="utf-8")
    assert "max-width:760px" in css and "max-width:1279px" in css
    assert "#E7A8B8" in css and "color-scheme:light" in css


def test_session_cookie_navigation_csrf_and_restart_invalidation(setup):
    service, _ = setup
    app = create_app(service, "fake-console-token")
    with TestClient(app, base_url="http://127.0.0.1") as web:
        assert web.get("/").status_code == 401
        assert (
            web.post("/api/session", headers={"Origin": "http://127.0.0.1"}).status_code
            == 401
        )
        response = web.post(
            "/api/session", headers={"Authorization": "Bearer fake-console-token"}
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "fake-console-token" not in cookie
        for path in PAGES:
            assert web.get(path).status_code == 200
        assert web.post("/api/validate", json={}).status_code == 403
        assert (
            web.post(
                "/api/validate", json={}, headers={"Origin": "http://localhost:9999"}
            ).status_code
            == 403
        )
        assert (
            web.get(
                "/api/state", headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        state = web.get("/api/state").json()
        assert web.post(
            "/api/save",
            json={"version": state["version"]},
            headers={"Origin": "http://127.0.0.1"},
        ).json()["ok"]
        with TestClient(
            create_app(service, "different-token"), base_url="http://127.0.0.1"
        ) as restarted:
            restarted.cookies.update(web.cookies)
            assert restarted.get("/models").status_code == 401


def test_migrated_forms_and_honest_empty_pages(setup):
    service, _ = setup
    with web_client(service) as web:
        chat = web.get("/chat").text
        assert 'id="allowlist"' in chat and 'id="add-qq"' in chat
        assert 'data-config="SI_QQ_ALLOWED_USER_IDS"' not in chat
        assert "当前版本暂未启用" in chat
        assert 'data-mode="SI_ONEBOT_ACCESS_TOKEN"' in chat
        models = web.get("/models").text
        for action in ("llm", "embedding", "reranker", "save", "validate"):
            assert f'data-action="{action}"' in models
        for name in ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY"):
            assert f'data-mode="{name}"' in models
        assert "身份锁定" in web.get("/character").text
        assert "还没有表情包" in web.get("/media").text
        assert "<button disabled>" in web.get("/media").text
        assert "未启用统计" in web.get("/").text
        assert web.get("/api/memories").json()["items"] == []
        assert not (service.project / "runtime").exists()


def test_readonly_memory_search_pagination_evidence_and_redaction(setup):
    service, manager = setup
    service.env.path.write_text("DEEPSEEK_API_KEY=fake-private-key\n", encoding="utf-8")
    dbpath = manager.paths().database
    dbpath.parent.mkdir()
    with closing(sqlite3.connect(dbpath)) as db:
        db.executescript(SCHEMA)
        for index in range(24):
            db.execute(
                "INSERT INTO memories(id,memory_type,content,source,salience,status,created_at) VALUES(?, 'semantic', ?, 'explicit', 'high','active', ?)",
                (
                    str(index),
                    f"喜欢故事 {index} <script>alert(1)</script> fake-private-key",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        db.execute(
            "INSERT INTO memory_evidence VALUES('0','archive_message','test-evidence')"
        )
        db.execute(
            "INSERT INTO memory_embeddings VALUES('0','test-model',1,?, '2026-01-01T00:00:00+00:00')",
            (b"\0\0\0\0",),
        )
        db.commit()
    before = sha256(dbpath.read_bytes()).digest()
    with web_client(service) as web:
        first = web.get("/api/memories").json()
        assert len(first["items"]) == 20 and first["more"]
        second = web.get("/api/memories?offset=20").json()
        assert len(second["items"]) == 4 and not second["more"]
        matched = web.get("/api/memories", params={"q": "故事 0"}).json()["items"][0]
        assert matched["evidence"][0]["evidence_ref"] == "test-evidence"
        assert matched["embeddings"][0]["dimensions"] == 1
        assert "fake-private-key" not in str(first)
        assert (
            web.get("/api/memories", params={"q": "' OR 1=1 --"}).json()["items"] == []
        )
        assert web.get("/api/memories?offset=-1").status_code == 400
        assert web.post("/api/memories", json={"delete": "0"}).status_code == 404
        overview = web.get("/api/overview").json()
        assert overview["memory_count"] == 24
        assert "2026-01-01" in overview["activity"]
    assert before == sha256(dbpath.read_bytes()).digest()
    assert list(dbpath.parent.iterdir()) == [dbpath]
    script = (ASSETS / "setup.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "textContent" in script


def test_query_only_connection_and_safe_database_errors(setup, monkeypatch):
    _, manager = setup
    path = manager.paths().database
    path.parent.mkdir()
    path.write_bytes(b"not a database")
    service = ConsoleService(manager)
    original = sqlite3.connect
    seen = []

    def connect(address, **kwargs):
        assert address.endswith("?mode=ro") and kwargs["uri"] is True
        db = original(address, **kwargs)
        db.set_trace_callback(seen.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        service.memories()
    assert "PRAGMA query_only = ON" in seen
    assert "未修改数据库" in service.overview()["activity"]


def test_runtime_delegation_fresh_config_and_safe_failures(setup, monkeypatch):
    service, manager = setup
    calls = []
    for action in ("start", "stop", "restart", "logs"):

        def invoke(action=action):
            calls.append((action, manager.effective().get("SI_LLM_MODEL")))
            return OperationResult(True, "done fake-sensitive-token")

        monkeypatch.setattr(manager, action, invoke)
    monkeypatch.setattr(
        manager, "check", lambda **kwargs: OperationResult(True, "本地检查")
    )
    service.env.path.write_text(
        "DEEPSEEK_API_KEY=fake-sensitive-token\n", encoding="utf-8"
    )
    with web_client(service) as web:
        snap = web.get("/api/state").json()
        assert web.post(
            "/api/save",
            json={
                "version": snap["version"],
                "values": {"SI_LLM_MODEL": "fresh-model"},
            },
        ).json()["ok"]
        for action in ("start", "stop", "restart", "logs", "health"):
            response = web.post(f"/api/{action}", json={})
            assert response.json()["ok"] and "fake-sensitive-token" not in response.text
        assert [entry[0] for entry in calls] == ["start", "stop", "restart", "logs"]
        assert all(entry[1] == "fresh-model" for entry in calls)

        def fail():
            raise RuntimeError("private traceback fake-sensitive-token")

        monkeypatch.setattr(manager, "start", fail)
        response = web.post("/api/start", json={})
        assert response.status_code == 400
        assert "RuntimeError" in response.text
        assert "private traceback" not in response.text
        assert "fake-sensitive-token" not in response.text


def test_overview_never_claims_chat_ready(setup, monkeypatch):
    service, manager = setup
    old = manager.status()
    monkeypatch.setattr(
        manager, "status", lambda: replace(old, runtime="Running", transport="qq")
    )
    service.env.path.write_text("SI_CHAT_TRANSPORT=qq\n", encoding="utf-8")
    with web_client(service) as web:
        data = web.get("/api/overview").json()
        assert data["status"]["runtime"] == "运行中"
        assert "未验证" in data["qq"]
        assert data["memory_count"] == "暂无数据"
        assert "QQ 已登录" not in str(data)
