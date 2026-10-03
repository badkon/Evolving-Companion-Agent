"""Headless first-run setup with temporary config and fake credentials/checks."""

import asyncio
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest.mock import patch

from textual.widgets import Button, Input, Select, Static

from evolving_companion.config_env import OperationResult
from evolving_companion.setup_app import SetupApp, SetupScreen
from evolving_companion.setup_services import SetupService


async def smoke(root: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    for directory in ("config", "data/characters", "runtime"):
        (root / directory).mkdir(parents=True)
    template = (repo / "config/si.env.example").read_text(encoding="utf-8")
    (root / "config/si.env.example").write_text(
        template.replace("runtime/si_001.db", (root / "runtime/si_001.db").as_posix()),
        encoding="utf-8",
    )
    seed = root / "data/characters/si_001.yaml"
    shutil.copyfile(repo / "data/characters/si_001.yaml", seed)
    before = seed.read_bytes()
    env_file = root / "config/si.env"
    service = SetupService(env_file, environment={}, project_root=root)
    secrets = {
        "DEEPSEEK_API_KEY": "fake-smoke-deepseek",
        "SILICONFLOW_API_KEY": "fake-smoke-silicon",
    }
    app = SetupApp(service)
    with patch.object(
        service,
        "run_checks",
        return_value=OperationResult(True, "Offline fake runtime_check OK", 0),
    ):
        async with app.run_test(size=(100, 45)) as pilot:
            await app.workers.wait_for_complete()
            screen = app.screen
            assert isinstance(screen, SetupScreen)
            assert "Missing" in str(screen.query_one("#welcome", Static).content)
            screen.query_one("#configure", Button).press()
            await pilot.pause()
            for name, value in secrets.items():
                widget = screen.query_one(f"#secret-{name}", Input)
                assert widget.password
                widget.value = value
            screen.query_one("#transport", Select).value = "qq"
            screen.query_one("#value-SI_QQ_BOT_USER_ID", Input).value = "202"
            screen.query_one("#value-SI_QQ_ALLOWED_USER_IDS", Input).value = "101"
            screen.query_one("#review-form", Button).press()
            await pilot.pause()
            assert all(
                value not in str(screen.query_one("#review-text", Static).content)
                for value in secrets.values()
            )
            assert not env_file.exists()
            screen.query_one("#save", Button).press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert service.last_result.saved and service.last_result.ready
            assert all(
                value not in service.last_result.message for value in secrets.values()
            )
            screen.query_one("#finish", Button).press()  # Finish configuration only.
            await pilot.pause()
            assert not app.is_running
    assert seed.read_bytes() == before
    assert not service.paths.database.exists()
    print(
        "PASS: missing config, masked fake keys, safe review, atomic save, fake runtime_check, finish, clean exit."
    )
    print(
        "Temporary config only; no real secrets, runtime DB, network, or runtime initialization used."
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="si-setup-smoke-") as directory:
        with (
            patch("socket.create_connection", side_effect=AssertionError("No network")),
            patch("subprocess.run", side_effect=AssertionError("No network")),
            patch("evolving_companion.config_env.protect_file"),
        ):
            asyncio.run(smoke(Path(directory)))
