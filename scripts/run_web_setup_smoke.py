"""Real loopback Console smoke; synthetic keys, temporary paths, no remote API."""

from pathlib import Path
import os
import shutil
from tempfile import TemporaryDirectory

import httpx

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
        finally:
            server.stop()
        assert not server.is_running and server.socket.fileno() == -1
        assert not (root / "runtime/si_001.db").exists()
        print(
            "PASS: real loopback Console -> authenticated eight routes -> protected save/check -> Character save -> clean shutdown; no API or runtime DB."
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


if __name__ == "__main__":
    main()
