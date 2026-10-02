"""Headless offline Manager smoke: fake status, keyboard navigation, clean exit."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from textual.widgets import OptionList, Static

from evolving_companion.deployment import CheckResult, DeploymentPaths
from evolving_companion.manager_app import SIManagerApp
from evolving_companion.manager_services import (
    DeploymentFacade,
    OperationResult,
    Overview,
    ServiceStatus,
)


class FakeService:
    def status(self) -> ServiceStatus:
        return ServiceStatus("Running", OperationResult(True, "active", 0))

    def start(self) -> OperationResult:
        return OperationResult(True, "fake start", 0)

    def stop(self) -> OperationResult:
        return OperationResult(True, "fake stop", 0)

    def restart(self) -> OperationResult:
        return OperationResult(True, "fake restart", 0)

    def recent_logs(self) -> OperationResult:
        return OperationResult(True, "fake local journal", 0)


class FakeDeployment(DeploymentFacade):
    def overview(self) -> Overview:
        return Overview(
            "玲 / SI-001",
            "masked…",
            "Running",
            "Available",
            "OK",
            "OK",
            "API / API",
            "qq",
            "fake",
        )

    def checks(self, *, health: bool = False) -> tuple[CheckResult, ...]:
        return (
            CheckResult(
                "Offline fake health" if health else "Offline fake check", True, "OK"
            ),
        )


async def smoke() -> None:
    unused = Path("unused-smoke-path")
    app = SIManagerApp(
        FakeDeployment(
            DeploymentPaths(unused, unused, unused, unused, unused), FakeService(), {}
        )
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await app.workers.wait_for_complete()
        assert "Running" in str(app.query_one("#output", Static).content)
        await pilot.press("down", "enter")
        await app.workers.wait_for_complete()
        assert app.page == "Service"
        app.query_one("#navigation", OptionList).highlighted = 3
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert app.page == "Health"
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert app.page == "Overview"
        await pilot.press("q")
        assert not app.is_running
    print(
        "PASS: app start, fake Running status, keyboard navigation, clean exit; no DB/secrets/network/systemd."
    )


if __name__ == "__main__":
    with (
        patch("socket.create_connection", side_effect=AssertionError("No network")),
        patch("subprocess.run", side_effect=AssertionError("No systemd")),
    ):
        asyncio.run(smoke())
