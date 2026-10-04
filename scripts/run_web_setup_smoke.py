"""Real loopback Console smoke; synthetic keys, temporary paths, no remote API."""

from pathlib import Path
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from evolving_companion.affective import EmotionEvent
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.storage import SQLiteStore
from evolving_companion.web_setup import PAGES, WebSetupServer
from evolving_companion.web_setup_services import WebSetupService


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="si-web-smoke-") as directory:
        root = Path(directory)
        for name in (
            "config/si.env.example",
            "data/characters/si_001.yaml",
            "data/worlds/si_world.yaml",
        ):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / name, target)
        (root / "runtime").mkdir()
        # Windows Python needs SystemRoot to initialize sockets in child checks.
        # Do not inherit any real API/Character configuration into this smoke.
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "SYSTEMROOT")
            if key in os.environ
        }
        service = WebSetupService(root / "config/si.env", root, environment)
        server = WebSetupServer(service)
        server.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.port}", timeout=60, trust_env=False
            ) as web:
                check_console(web, server)
                assert not (root / "runtime/si_001.db").exists()
                check_affective_console(web, root)
        finally:
            server.stop()
        assert not server.is_running and server.socket.fileno() == -1
        print(
            "PASS: real loopback Console -> authenticated nine routes -> protected save/check -> Character save -> read-only affective dashboard/detail -> clean shutdown; no API, temporary synthetic DB only."
        )


def check_console(web: httpx.Client, server: WebSetupServer) -> None:
    assert web.get("/").status_code == 401
    response = web.post(
        "/api/session", headers={"Authorization": f"Bearer {server.token}"}
    )
    assert response.status_code == 200
    web.headers["Origin"] = f"http://127.0.0.1:{server.port}"
    for path in PAGES:
        page = web.get(path)
        assert page.status_code == 200 and "SI Console" in page.text
    for asset in ("setup.js", "setup.css"):
        assert web.get(f"/static/{asset}").status_code == 200
    assert web.get("/api/memories").json()["items"] == []
    assert web.get("/api/affective").json()["status"] == "empty"
    assert web.post("/api/affective", json={"trust": 1}).status_code == 405
    state = web.get("/api/state").json()
    payload = {
        "version": state["version"],
        "secrets": {
            name: {"mode": "replace", "value": "fake-smoke-key"}
            for name in ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY")
        },
    }
    assert web.post("/api/save", json=payload).json()["ok"]
    result = web.post("/api/validate", json={}).json()
    assert result["ok"], result["message"]
    overview = web.get("/api/overview")
    assert overview.status_code == 200 and "fake-smoke-key" not in overview.text
    assert overview.json()["database_size"] == "尚未初始化"
    assert "fake-smoke-key" not in web.get("/api/state").text
    assert web.post(
        "/api/character",
        json={
            "version": state["character_version"],
            "character": {"working_name": "测试角色"},
        },
    ).json()["ok"]


def check_affective_console(web: httpx.Client, root: Path) -> None:
    database = root / "runtime/si_001.db"
    SQLiteStore(database)
    character = load_character_seed_data(
        root / "data/characters/si_001.yaml"
    ).identity.internal_id
    target, now = uuid4(), datetime.now(timezone.utc)
    AffectiveStore(database, character, target, now)
    emotion = EmotionEvent(
        type="joy",
        intensity=0.4,
        target=target,
        cause_summary="Console smoke 合成事件；不使用真实聊天。",
        created_at=now,
        decay_until=now + timedelta(hours=1),
        source_event_id=uuid4(),
    )
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO emotion_events VALUES(?,?,?,?,?)",
            (
                str(character),
                str(emotion.source_event_id),
                emotion.type,
                emotion.decay_until.isoformat(),
                emotion.model_dump_json(),
            ),
        )
    before = sha256(database.read_bytes()).digest()
    data = web.get("/api/affective").json()
    assert data["relationship"]["stage_label"] == "熟人"
    assert data["relationship"]["romantic"] is False
    assert data["mood"]["dimensions"][0]["value"] == 0.15
    assert data["emotions"][0]["label"] == "开心"
    assert web.get("/api/overview").json()["affective"]["active_count"] == 1
    assert web.post("/api/affective", json={"reset": True}).status_code == 405
    assert before == sha256(database.read_bytes()).digest()


if __name__ == "__main__":
    main()
