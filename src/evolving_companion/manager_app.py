"""Keyboard-first Textual Manager; no direct process or config operations."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import logging

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, OptionList, Static

from evolving_companion.config_env import OperationResult
from evolving_companion.manager_services import ManagerService
from evolving_companion.setup_app import SetupScreen

PAGES = ("Start", "Stop", "Restart", "Status", "Logs", "Configure", "Exit")


class SIManagerApp(App[None]):
    TITLE = "SI-001 Manager"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("q", "safe_quit", "退出 Manager"),
        ("r", "refresh_page", "刷新"),
        ("escape", "back", "Status"),
    ]
    CSS = """
    #navigation { width: 25; }
    #body { width: 1fr; padding: 1; }
    #output, #result { height: auto; }
    """

    def __init__(self, service: ManagerService) -> None:
        super().__init__()
        self.service = service
        self.page = "Status"
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield OptionList(*PAGES, id="navigation")
            with VerticalScroll(id="body"):
                yield Static("", id="output", markup=False)
                yield Static("", id="result", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#navigation", OptionList).highlighted = PAGES.index("Status")
        self.query_one("#navigation", OptionList).focus()
        self.action_refresh_page()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.busy or event.option_list.id != "navigation":
            return
        choice = PAGES[event.option_index]
        if choice == "Exit":
            self.action_safe_quit()
        elif choice == "Configure":
            self.configure()
        elif choice in {"Start", "Stop", "Restart"}:
            operation = {
                "Start": self.service.start,
                "Stop": self.service.stop,
                "Restart": self.service.restart,
            }[choice]
            self.perform(operation)
        else:
            self.page = choice
            self.action_refresh_page()

    def _page_text(self) -> str:
        if self.page == "Logs":
            result = self.service.logs()
            return "Recent Logs (max 100 lines / 64 KiB; r refresh)\n" + result.message
        return "Status (local checks, not chat readiness)\n" + "\n".join(
            f"{key}: {value}" for key, value in asdict(self.service.status()).items()
        )

    @work
    async def perform(self, operation: Callable[[], str | OperationResult]) -> None:
        if self.busy:
            return
        self.busy = True
        self.query_one("#navigation").disabled = True
        try:
            result = await asyncio.to_thread(operation)
            if isinstance(result, OperationResult):
                self.query_one("#result", Static).update(result.message)
                self.page = "Status"
                text = await asyncio.to_thread(self._page_text)
            else:
                text = result
            self.query_one("#output", Static).update(text)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Manager operation failed (%s)", type(error).__name__
            )
            self.query_one("#result", Static).update(
                f"Operation failed ({type(error).__name__}); check local configuration/permissions."
            )
        finally:
            self.busy = False
            self.query_one("#navigation").disabled = False
            self.query_one("#navigation", OptionList).focus()

    @work
    async def configure(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            setup = await asyncio.to_thread(self.service.setup_service)
            self.push_screen(SetupScreen(setup), self._configured)
        except Exception as error:
            self.query_one("#result", Static).update(
                f"Configure unavailable ({type(error).__name__})."
            )
        finally:
            self.busy = False

    def _configured(self, saved: bool | None) -> None:
        self.page = "Status"
        self.action_refresh_page()

    def action_refresh_page(self) -> None:
        if not self.busy and not isinstance(self.screen, SetupScreen):
            self.perform(self._page_text)

    def action_back(self) -> None:
        if not self.busy and not isinstance(self.screen, SetupScreen):
            self.page = "Status"
            self.query_one("#navigation", OptionList).highlighted = PAGES.index(
                "Status"
            )
            self.action_refresh_page()

    def action_safe_quit(self) -> None:
        if isinstance(self.screen, SetupScreen):
            self.screen.action_cancel()
        elif self.busy:
            self.notify(
                "Operation in progress; wait before exiting.", severity="warning"
            )
        else:
            self.exit()  # Intentionally never stop the independent Core process.
