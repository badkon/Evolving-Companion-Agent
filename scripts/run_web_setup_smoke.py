"""Offline Web save/validate smoke. Synthetic keys and temporary paths only."""

from pathlib import Path
import os
import shutil
from tempfile import TemporaryDirectory

from starlette.testclient import TestClient

from evolving_companion.web_setup import create_app
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
        with TestClient(
            create_app(service, "fake-smoke-token"),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer fake-smoke-token"},
        ) as web:
            assert web.get("/").status_code == 200
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
            assert "fake-smoke-key" not in web.get("/api/state").text
            assert web.post(
                "/api/character",
                json={
                    "version": state["character_version"],
                    "character": {"working_name": "测试角色"},
                },
            ).json()["ok"]
        assert not (root / "runtime/si_001.db").exists()
        print(
            "PASS: authenticated render -> protected save -> real offline runtime check -> Character save; no API or runtime DB."
        )


if __name__ == "__main__":
    main()
