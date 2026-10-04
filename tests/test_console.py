"""Console uses synthetic config/DB and fake runtime; never calls provider APIs."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sqlite3
import json
import subprocess
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from evolving_companion.config_env import OperationResult
from evolving_companion.affective import EmotionEvent, EmotionType, Mood
from evolving_companion.affective_console import AffectiveConsoleService
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.clock import FixedClock
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


@pytest.fixture
def affective_setup(setup, monkeypatch):
    from evolving_companion import affective_console

    service, manager = setup
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    clock = FixedClock(now)
    monkeypatch.setattr(affective_console, "SystemClock", lambda: clock)
    character = load_character_seed_data(
        service.project / "data/characters/si_001.yaml"
    ).identity.internal_id
    target = uuid4()
    database = manager.paths().database
    database.parent.mkdir()
    AffectiveStore(database, character, target, now)
    reader = AffectiveConsoleService(database, character, clock)
    return service, manager, reader, clock, target


def write_console_emotion(
    reader: AffectiveConsoleService,
    target: UUID,
    now: datetime,
    *,
    kind: EmotionType = "curiosity",
    cause: str = "合成原因",
) -> EmotionEvent:
    emotion = EmotionEvent(
        type=kind,
        intensity=0.6,
        target=target,
        cause_summary=cause,
        created_at=now,
        decay_until=now + timedelta(hours=2),
        source_event_id=uuid4(),
    )
    with closing(sqlite3.connect(reader.database)) as db, db:
        db.execute(
            "INSERT INTO emotion_events VALUES(?,?,?,?,?)",
            (
                reader.character_id,
                str(emotion.source_event_id),
                emotion.type,
                emotion.decay_until.isoformat(),
                emotion.model_dump_json(),
            ),
        )
    return emotion


def test_affective_relationship_mood_and_emotion_read_without_writes(affective_setup):
    service, _, reader, clock, target = affective_setup
    write_console_emotion(reader, target, clock.now_utc())
    clock.advance(minutes=30)
    before = sha256(reader.database.read_bytes()).digest()
    with web_client(service) as web:
        data = web.get("/api/affective").json()
        assert data["status"] == "ready"
        relationship = data["relationship"]
        assert relationship["stage_label"] == "熟人"
        assert relationship["romantic"] is False
        assert relationship["boundary"] == "关系边界：非恋爱关系"
        assert [d["percent"] for d in relationship["dimensions"]] == [
            75,
            65,
            65,
            80,
            15,
        ]
        assert data["mood"]["dimensions"][0]["value"] == 0.15
        emotion = data["emotions"][0]
        assert emotion["label"] == "好奇" and emotion["active"]
        assert emotion["intensity"] == pytest.approx(0.45)
        assert emotion["initial_intensity"] == 0.6
        assert emotion["cause_summary"] == "合成原因"
        assert emotion["created_at"].endswith("+00:00")
        assert data["debug"]["primary_target"] == str(target)
        assert web.get("/api/affective").json() == data
        overview = web.get("/api/overview").json()
        assert overview["affective"] == data
        for path in ("/", "/affective"):
            html = web.get(path).text
            assert "当前状态" in html
            assert str(target) not in html
            assert Markup(html).avatars == 1
        assert 'id="affective-summary"' in web.get("/").text
        page = web.get("/affective").text
        assert 'id="refresh-affective"' in page
        assert "近期情绪" in page and "调试信息（只读）" in page
        assert "data-action=" not in page
    assert sha256(reader.database.read_bytes()).digest() == before
    assert list(reader.database.parent.iterdir()) == [reader.database]


def test_mood_signed_values_normalized_and_recovered_only_in_memory(affective_setup):
    _, _, reader, clock, _ = affective_setup
    mood = Mood(
        valence=-1,
        energy=0,
        calmness=1,
        sociability=-0.5,
        updated_at=clock.now_utc(),
    )
    with closing(sqlite3.connect(reader.database)) as db, db:
        db.execute(
            "UPDATE affective_state SET payload=? WHERE character_id=?",
            (mood.model_dump_json(), reader.character_id),
        )
    before = sha256(reader.database.read_bytes()).digest()
    data = reader.read()
    assert [d["position"] for d in data["mood"]["dimensions"]] == pytest.approx(
        [0, 50, 100, 25]
    )
    assert [d["value"] for d in data["mood"]["dimensions"]] == pytest.approx(
        [-1, 0, 1, -0.5]
    )
    assert "整体偏低落" in data["mood"]["summary"]
    clock.advance(hours=6)
    recovered = reader.read()["mood"]
    assert recovered["dimensions"][0]["value"] == pytest.approx(-0.425)
    assert recovered["updated_at"] == mood.updated_at.isoformat()
    assert sha256(reader.database.read_bytes()).digest() == before


def test_recent_emotion_limit_active_ranking_and_empty_active_state(affective_setup):
    _, _, reader, clock, target = affective_setup
    now = clock.now_utc()
    for index in range(15):
        write_console_emotion(
            reader,
            target,
            now - timedelta(minutes=index),
            kind="joy" if index % 2 else "curiosity",
        )
    data = reader.read()
    assert len(data["emotions"]) == 12
    assert len(data["active_emotions"]) == 2
    assert data["active_count"] == 15
    assert data["active_emotions"][0]["label"] == "好奇"
    clock.advance(hours=3)
    expired = reader.read()
    assert expired["active_emotions"] == [] and expired["active_count"] == 0
    assert len(expired["emotions"]) == 12
    assert all(not e["active"] and e["intensity"] == 0 for e in expired["emotions"])


def test_missing_relationship_mood_and_primary_do_not_bootstrap(affective_setup):
    _, _, reader, _, _ = affective_setup
    with closing(sqlite3.connect(reader.database)) as db, db:
        db.execute("DELETE FROM relationship_states")
        db.execute("DELETE FROM affective_state")
    before = sha256(reader.database.read_bytes()).digest()
    data = reader.read()
    assert data["relationship"] is None and data["mood"] is None
    assert data["emotions"] == []
    assert sha256(reader.database.read_bytes()).digest() == before
    with closing(sqlite3.connect(reader.database)) as db, db:
        db.execute("DELETE FROM affective_primary_target")
    assert reader.read()["debug"]["primary_target"] == "尚未绑定"


@pytest.mark.parametrize("database_kind", ["missing", "empty", "legacy", "corrupt"])
def test_affective_empty_or_bad_database_keeps_pages_available(setup, database_kind):
    service, manager = setup
    path = manager.paths().database
    if database_kind != "missing":
        path.parent.mkdir()
        if database_kind == "corrupt":
            path.write_bytes(b"not a database")
        else:
            with closing(sqlite3.connect(path)) as db:
                if database_kind == "legacy":
                    db.executescript(SCHEMA)
    before = path.read_bytes() if path.is_file() else None
    with web_client(service) as web:
        for page in ("/", "/affective"):
            assert web.get(page).status_code == 200
        data = web.get("/api/affective").json()
        assert data["status"] == ("error" if database_kind == "corrupt" else "empty")
        assert data["relationship"] is None and data["mood"] is None
        if database_kind == "corrupt":
            assert "状态读取失败" in data["message"]
        else:
            assert "尚未初始化" in data["message"]
    assert (path.read_bytes() if path.is_file() else None) == before


def test_primary_binding_is_not_inferred_from_allowlist_and_other_character_isolated(
    affective_setup,
):
    service, _, reader, clock, target = affective_setup
    service.env.path.write_text("SI_QQ_ALLOWED_USER_IDS=200,100\n", encoding="utf-8")
    AffectiveStore(reader.database, uuid4(), uuid4(), clock.now_utc())
    with web_client(service) as web:
        data = web.get("/api/affective").json()
        assert data["debug"]["primary_target"] == str(target)
        assert data["relationship"]["stage"] == "familiar"


def test_affective_read_failure_safe_logs_and_secret_redaction(
    affective_setup,
    monkeypatch,
    caplog,
):
    service, _, reader, clock, target = affective_setup
    service.env.path.write_text(
        "DEEPSEEK_API_KEY=fake-affective-secret\n", encoding="utf-8"
    )
    write_console_emotion(
        reader,
        target,
        clock.now_utc(),
        cause="<script>alert(1)</script> fake-affective-secret " + "合成摘要" * 30,
    )
    with web_client(service) as web:
        response = web.get("/api/affective")
        assert "fake-affective-secret" not in response.text
        assert "<script>" in response.json()["emotions"][0]["cause_summary"]
        assert response.headers["cache-control"] == "no-store"
        assert web.get("/api/overview").json()["affective"]["status"] == "ready"

        def fail(_):
            raise PermissionError("private traceback fake-affective-secret")

        monkeypatch.setattr(AffectiveConsoleService, "read", fail)
        failed = web.get("/api/affective")
        assert failed.status_code == 200 and failed.json()["status"] == "error"
        assert "fake-affective-secret" not in failed.text + caplog.text
        assert "private traceback" not in failed.text + caplog.text
        assert "PermissionError" in caplog.text
        assert web.get("/api/overview").json()["affective"]["status"] == "error"


def test_affective_api_authenticated_get_only_and_database_connection_readonly(
    affective_setup,
    monkeypatch,
):
    service, _, reader, _, _ = affective_setup
    original = sqlite3.connect
    seen = []

    def connect(address, **kwargs):
        assert address.endswith("?mode=ro") and kwargs["uri"]
        db = original(address, **kwargs)
        db.set_trace_callback(seen.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    before = sha256(reader.database.read_bytes()).digest()
    with web_client(service) as web:
        assert web.get("/api/affective").status_code == 200
        assert (
            web.get("/api/affective", headers={"Authorization": "bad"}).status_code
            == 401
        )
        assert (
            web.get(
                "/api/affective", headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = web.request(method, "/api/affective", json={"trust": 1})
            assert response.status_code == 405
        assert "PRAGMA query_only = ON" in seen
        assert all(sql.split()[0] in {"SELECT", "PRAGMA", "BEGIN"} for sql in seen)
    assert sha256(reader.database.read_bytes()).digest() == before


def test_affective_browser_renderer_signed_bars_safe_text_and_empty_states(
    affective_setup,
):
    """Exercise the actual native JS renderer with a small DOM, no browser/network."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is only used for the optional native JS renderer check")
    _, _, reader, clock, target = affective_setup
    write_console_emotion(
        reader, target, clock.now_utc(), cause="<script>unsafe</script>" + "长摘要" * 40
    )
    data = reader.read()
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.attrs={}; this.dataset={}; this.textContent=''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; this.textContent=''; }
  setAttribute(key,value) { this.attrs[key]=value; }
  addEventListener() {}
}
const ids=Object.fromEntries(['result','affective-summary','relationship-state','mood-state','affective-note','emotions','emotion-note','affective-debug'].map(id=>['#'+id,new Element('div')]));
const document={body:{dataset:{page:'/affective',login:true}},createElement:tag=>new Element(tag),querySelectorAll:s=>ids[s]?[ids[s]]:[],querySelector:s=>s==='#affective-summary, #relationship-state'?ids['#affective-summary']:ids[s]||null};
const context={document,location:{hash:'',pathname:'/affective'},history:{replaceState(){}},console};
vm.createContext(context); vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.data=JSON.parse(process.argv[2]); vm.runInContext('renderAffective(data)',context);
function flatten(element) { return [element,...element.children.flatMap(flatten)]; }
const elements=Object.values(ids).flatMap(flatten);
assert(elements.some(e=>e.textContent==='熟人 · 相处放松'));
assert(elements.some(e=>e.textContent==='关系边界：非恋爱关系'));
assert(elements.some(e=>e.textContent.includes('<script>unsafe</script>')));
assert(!elements.some(e=>e.tag==='script' || e.tag==='input' || e.tag==='form'));
assert(elements.filter(e=>e.tag==='progress').length===9);
assert(elements.some(e=>e.tag==='summary' && e.textContent==='展开原因摘要'));
context.data.mood.dimensions[0]={key:'valence',label:'整体情绪',value:-1,position:0};
vm.runInContext('renderAffective(data)',context);
assert(flatten(ids['#mood-state']).some(e=>e.textContent==='-1.00'));
assert(flatten(ids['#mood-state']).some(e=>e.tag==='progress' && e.value===0));
context.data.relationship=null; context.data.mood=null; context.data.emotions=[]; context.data.active_emotions=[]; context.data.active_count=0;
vm.runInContext('renderAffective(data)',context);
assert(flatten(ids['#relationship-state']).some(e=>e.textContent==='暂未建立关系状态'));
assert(flatten(ids['#emotion-note']).some(e=>e.textContent==='当前没有活跃情绪事件'));
context.data.status='error'; context.data.message='状态读取失败';
vm.runInContext('renderAffective(data)',context);
assert(flatten(ids['#affective-summary']).some(e=>e.textContent==='状态读取失败'));
console.log('Renderer: signed bars, text escaping, folded cause, empty/error states passed');
"""
    checked = subprocess.run(
        [
            node,
            "-e",
            script,
            str(ASSETS / "setup.js"),
            json.dumps(data, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert checked.returncode == 0, checked.stderr
