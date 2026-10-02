"""Small keyboard-first Textual operations UI; blocking work stays off UI loop."""

import asyncio
from dataclasses import asdict
import logging
from typing import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, OptionList, Static

from evolving_companion.manager_services import DeploymentFacade, OperationResult

PAGES = (
    "Overview",
    "Service",
    "Deployment Check",
    "Health",
    "Configuration",
    "Backup / Restore",
    "Logs",
    "Exit",
)


class RestoreConfirmation(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel"), ("q", "cancel", "Cancel")]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.backup_name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmation"):
            yield Static(
                f"Restore {self.backup_name}?\n这将替换当前 runtime DB。\n"
                "必须已停止服务及其他 writer；先生成 pre_restore 备份。",
                markup=False,
            )
            yield Button("Cancel / 取消", id="cancel")
            yield Button("Confirm / 确认恢复", id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm")


class SIManagerApp(App[None]):
    TITLE = "SI Manager — 本地运维"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("q", "safe_quit", "退出"),
        ("r", "refresh_page", "刷新"),
        ("escape", "back", "返回"),
    ]
    CSS = """
    #navigation { width: 25; }
    #body { width: 1fr; padding: 1; }
    #output { height: auto; }
    #backups { height: 10; }
    #controls { height: auto; }
    #controls Button { min-width: 14; }
    RestoreConfirmation { align: center middle; }
    #confirmation { width: 65; height: auto; padding: 2; background: $surface; border: solid $error; }
    #confirmation Static { height: auto; margin-bottom: 1; }
    """

    def __init__(self, facade: DeploymentFacade) -> None:
        super().__init__()
        self.facade = facade
        self.page = "Overview"
        self.busy = False
        self.backup_names: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield OptionList(*PAGES, id="navigation")
            with VerticalScroll(id="body"):
                yield Static("", id="output", markup=False)
                yield OptionList(id="backups")
                with Horizontal(id="controls"):
                    yield Button("Start", id="start")
                    yield Button("Stop", id="stop")
                    yield Button("Restart", id="restart")
                    yield Button("Create Backup", id="backup")
                    yield Button("Restore", id="restore", variant="error")
                yield Static("", id="result", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#navigation", OptionList).focus()
        self.action_refresh_page()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "navigation" or self.busy:
            return
        self.page = PAGES[event.option_index]
        if self.page == "Exit":
            self.action_safe_quit()
        else:
            self.action_refresh_page()

    def action_back(self) -> None:
        if not self.busy:
            self.page = "Overview"
            navigation = self.query_one("#navigation", OptionList)
            navigation.highlighted = 0
            navigation.focus()
            self.action_refresh_page()

    def action_safe_quit(self) -> None:
        if self.busy:
            self.notify("操作进行中，请等待完成后退出。", severity="warning")
        else:
            self.exit()

    def action_refresh_page(self) -> None:
        if self.busy or isinstance(self.screen, RestoreConfirmation):
            return
        for button in self.query("#controls Button"):
            button.display = (
                self.page == "Service" and button.id in {"start", "stop", "restart"}
            ) or (
                self.page == "Backup / Restore" and button.id in {"backup", "restore"}
            )
        self.query_one("#backups").display = self.page == "Backup / Restore"
        self._perform(self._page_text)

    def _page_text(self) -> str:
        if self.page == "Overview":
            return "Overview\n" + "\n".join(
                f"{name}: {value}"
                for name, value in asdict(self.facade.overview()).items()
            )
        if self.page == "Service":
            status = self.facade.service.status()
            return f"Character Core Service: {status.state}\n{status.result.message}"
        if self.page in {"Deployment Check", "Health"}:
            text = "\n".join(
                f"{item.name}: {'OK' if item.ok else 'ERROR'} — {item.status}"
                for item in self.facade.checks(health=self.page == "Health")
            )
            return (
                self.page
                + "\n"
                + text
                + "\n离线检查不代表 DeepSeek / SiliconFlow / QQ 在线。"
            )
        if self.page == "Configuration":
            return (
                "Configuration（只读）\n"
                + "\n".join(
                    f"{name}: {value}"
                    for name, value in self.facade.configuration().items()
                )
                + "\nSecrets 请通过受信任的外部编辑器管理。配置变化需退出 Manager 后重新进入。"
            )
        if self.page == "Backup / Restore":
            records = self.facade.backups()
            self.backup_names = tuple(record.name for record in records)
            return f"Runtime DB: {self.facade.paths.database}\n" + "\n".join(
                f"{record.name} | {record.created_at} | {record.size} bytes"
                for record in records
            )
        result = self.facade.service.recent_logs()
        return "Recent Logs（最多 100 行；r 刷新）\n" + result.message

    @work
    async def _perform(self, operation: Callable[[], str | OperationResult]) -> None:
        if self.busy:
            return
        self.busy = True
        self.query_one("#navigation").disabled = True
        for button in self.query("#controls Button"):
            button.disabled = True
        try:
            result = await asyncio.to_thread(operation)
            if isinstance(result, OperationResult):
                self.query_one("#result", Static).update(result.message)
                if not result.ok:
                    self.notify(result.message, severity="error")
                # Backup and service actions always refresh local metadata/status.
                text = await asyncio.to_thread(self._page_text)
            else:
                text = result
            self.query_one("#output", Static).update(text)
            if self.page == "Backup / Restore":
                self.query_one("#backups", OptionList).clear_options().add_options(
                    self.backup_names
                )
        except Exception as error:
            safe = f"操作失败 ({type(error).__name__})；检查配置、权限与服务状态。"
            logging.getLogger(__name__).warning(
                "Manager operation failed (%s)", type(error).__name__
            )
            self.query_one("#result", Static).update(safe)
            self.notify(safe, severity="error")
        finally:
            self.busy = False
            self.query_one("#navigation").disabled = False
            self.query_one("#navigation", OptionList).focus()
            for button in self.query("#controls Button"):
                button.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.busy:
            return
        action = event.button.id
        if action in {"start", "stop", "restart"}:
            operation = {
                "start": self.facade.service.start,
                "stop": self.facade.service.stop,
                "restart": self.facade.service.restart,
            }[action]
            self._perform(operation)
        elif action == "backup":
            self._perform(self.facade.create_backup)
        elif action == "restore":
            index = self.query_one("#backups", OptionList).highlighted
            if index is None or index >= len(self.backup_names):
                self.notify("请先选择备份。", severity="warning")
                return
            name = self.backup_names[index]

            def confirmed(answer: bool | None) -> None:
                if answer:
                    self._perform(lambda: self.facade.restore(name, confirmed=True))

            self.push_screen(RestoreConfirmation(name), confirmed)
