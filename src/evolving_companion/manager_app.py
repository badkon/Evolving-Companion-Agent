"""Keyboard-first Textual Manager; no direct process or config operations."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import logging

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, OptionList, Static

from evolving_companion.config_env import OperationResult
from evolving_companion.manager_services import ManagerService
from evolving_companion.setup_app import SetupScreen
from evolving_companion.ui_text import FIELD_LABELS, PAGE_LABELS, display_value

PAGES = ("Start", "Stop", "Restart", "Status", "Logs", "Configure", "Exit")


class SIManagerApp(App[None]):
    TITLE = "SI 管理器"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("q", "safe_quit", "退出管理器"),
        ("r", "refresh_page", "刷新"),
        ("escape", "back", "返回状态"),
    ]
    CSS = """
    #navigation { width: 25; }
    #body { width: 1fr; padding: 1; }
    #output, #result { height: auto; }
    Screen.compact #main-layout { layout: vertical; }
    Screen.compact #navigation { width: 100%; height: 9; }
    Screen.compact #body { width: 100%; height: 1fr; }
    """

    def __init__(self, service: ManagerService) -> None:
        super().__init__()
        self.service = service
        self.page = "Status"
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-layout"):
            yield OptionList(*(PAGE_LABELS[page] for page in PAGES), id="navigation")
            with VerticalScroll(id="body"):
                yield Static("", id="output", markup=False)
                yield Static("", id="result", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.screen.set_class(self.size.width < 70, "compact")
        self.query_one("#navigation", OptionList).highlighted = PAGES.index("Status")
        self.query_one("#navigation", OptionList).focus()
        self.action_refresh_page()

    def on_resize(self, event: events.Resize) -> None:
        self.screen_stack[0].set_class(event.size.width < 70, "compact")

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
            return "最近日志（最多 100 行 / 64 KiB；r 刷新）\n" + result.message
        return "状态（仅本地检查，不代表聊天已就绪）\n" + "\n".join(
            f"{FIELD_LABELS[key]}：{'尚未创建' if key == 'runtime_db' and value == 'Missing' else display_value(value)}"
            for key, value in asdict(self.service.status()).items()
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
                f"操作失败（{type(error).__name__}）；请检查本地配置和权限。"
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
                f"无法打开配置（{type(error).__name__}）。"
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
            self.notify("操作进行中，请等待完成后再退出。", severity="warning")
        else:
            self.exit()  # Intentionally never stop the independent Core process.
