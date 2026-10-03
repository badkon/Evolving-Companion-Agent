"""Application configuration Screen. No direct filesystem or runtime startup I/O."""

import asyncio
import logging

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Select, Static

from evolving_companion.config_env import SECRET_FIELDS
from evolving_companion.setup_services import SetupService


class SetupScreen(Screen[bool]):
    BINDINGS = [("escape", "cancel", "取消 / 返回")]
    CSS = """
    #setup-body { padding: 1 2; }
    #setup-body Static { height: auto; margin-bottom: 1; }
    #setup-body Horizontal { height: auto; }
    #setup-body Button { margin-right: 1; }
    #setup-body Input, #setup-body Select { margin-bottom: 1; }
    """

    def __init__(self, service: SetupService) -> None:
        super().__init__()
        self.service = service
        self.busy = False
        self.saved = False
        self.pending: dict[
            str, str
        ] = {}  # Private write payload; never rendered/repr logged.

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="setup-body"):
            yield Static(
                "First-run Setup — Application configuration",
                id="welcome",
                markup=False,
            )
            with Horizontal(id="welcome-actions"):
                yield Button("Review", id="existing-review")
                yield Button("Configure / Reconfigure", id="configure")
                yield Button("Exit", id="exit-welcome")
            with VerticalScroll(id="form"):
                yield Static(
                    "LLM: DeepSeek API\nMemory: SiliconFlow / API / API（不安装本地 ML）",
                    markup=False,
                )
                for name in SECRET_FIELDS:
                    yield Static(name, id=f"status-{name}", markup=False)
                    yield Select(
                        [("Keep existing", "keep"), ("Replace", "replace")],
                        value="keep",
                        allow_blank=False,
                        id=f"mode-{name}",
                    )
                    yield Input(
                        password=True,
                        placeholder="输入新凭据；不会回填已有值",
                        id=f"secret-{name}",
                    )
                for name in ("SI_MEMORY_EMBEDDING_MODEL", "SI_MEMORY_RERANKER_MODEL"):
                    yield Static(name, markup=False)
                    yield Input(id=f"value-{name}")
                yield Static("Transport: QQ / None", markup=False)
                yield Select(
                    [("QQ OneBot", "qq"), ("None — 暂不配置聊天连接", "none")],
                    value="none",
                    allow_blank=False,
                    id="transport",
                )
                for name in (
                    "SI_ONEBOT_WS_URL",
                    "SI_QQ_BOT_USER_ID",
                    "SI_QQ_ALLOWED_USER_IDS",
                ):
                    yield Static(name, markup=False)
                    yield Input(id=f"value-{name}")
                yield Static("", id="runtime-path", markup=False)
                with Horizontal():
                    yield Button("Review", id="review-form")
                    yield Button("Cancel", id="cancel-form")
            with VerticalScroll(id="review"):
                yield Static("", id="review-text", markup=False)
                with Horizontal():
                    yield Button(
                        "Confirm — Save Configuration", id="save", variant="primary"
                    )
                    yield Button("Back", id="back-form")
                    yield Button("Cancel", id="cancel-review")
            with VerticalScroll(id="summary"):
                yield Static("", id="summary-text", markup=False)
                with Horizontal():
                    yield Button("No / Finish", id="finish")
            yield Static("", id="setup-error", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._show("welcome")
        self.prepare()

    def _show(self, stage: str) -> None:
        self.query_one("#welcome-actions").display = stage == "welcome"
        for name in ("form", "review", "summary"):
            self.query_one(f"#{name}").display = name == stage

    @work
    async def prepare(self) -> None:
        self.busy = True
        try:
            detection = await asyncio.to_thread(self.service.detect)
            self.query_one("#welcome", Static).update(
                f"First-run Setup\n{detection.message}\nCharacter: {detection.character}\nInternal ID: {detection.internal_identity}"
            )
            defaults = self.service.form_defaults()
            for name, value in defaults.items():
                if name != "SI_CHAT_TRANSPORT":
                    self.query_one(f"#value-{name}", Input).value = value
            transport = defaults["SI_CHAT_TRANSPORT"]
            self.query_one("#transport", Select).value = (
                transport if transport in {"qq", "none"} else "none"
            )
            for name, status in self.service.secret_status().items():
                self.query_one(f"#status-{name}", Static).update(f"{name}: {status}")
                mode = "keep" if status == "Configured" else "replace"
                self.query_one(f"#mode-{name}", Select).value = mode
                self.query_one(f"#secret-{name}", Input).disabled = mode == "keep"
            self.query_one("#runtime-path", Static).update(
                f"Runtime DB（只读）: {self.service.paths.database}"
            )
            self.query_one("#configure", Button).focus()
        except Exception as error:
            self._error(error)
        finally:
            self.busy = False

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id and event.select.id.startswith("mode-"):
            name = event.select.id.removeprefix("mode-")
            self.query_one(f"#secret-{name}", Input).disabled = event.value != "replace"

    def _collect(self) -> dict[str, str]:
        values = {
            "SI_MEMORY_EMBEDDING_PROVIDER": "api",
            "SI_MEMORY_RERANKER_PROVIDER": "api",
            "SI_CHAT_TRANSPORT": str(self.query_one("#transport", Select).value),
        }
        for name in (
            "SI_MEMORY_EMBEDDING_MODEL",
            "SI_MEMORY_RERANKER_MODEL",
            "SI_ONEBOT_WS_URL",
            "SI_QQ_BOT_USER_ID",
            "SI_QQ_ALLOWED_USER_IDS",
        ):
            values[name] = self.query_one(f"#value-{name}", Input).value.strip()
        for name in SECRET_FIELDS:
            if self.query_one(f"#mode-{name}", Select).value == "replace":
                values[name] = self.query_one(f"#secret-{name}", Input).value.strip()
        return values

    def _review(self, *, existing: bool = False) -> None:
        self.pending = {} if existing else self._collect()
        if not existing:
            self.service.validate(self.pending)
        review = self.service.review(self.pending)
        self.query_one("#review-text", Static).update(
            "Review — Save Configuration?\n"
            + "\n".join(f"{name}: {value}" for name, value in review.items())
            + "\nExisting configuration will be backed up before replacement."
        )
        self.query_one("#save", Button).display = not existing
        self._show("review")
        self.query_one("#back-form", Button).focus()

    def _error(self, error: Exception) -> None:
        # Exception messages can contain private input; only type reaches logs/UI.
        logging.getLogger(__name__).warning(
            "Setup operation failed (%s)", type(error).__name__
        )
        self.query_one("#setup-error", Static).update(
            f"操作失败 ({type(error).__name__})；检查配置字段、文件权限或应用配置。"
        )

    @work
    async def save_config(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            result = await asyncio.to_thread(self.service.save, self.pending)
            self.saved = result.saved
            self.query_one("#summary-text", Static).update(result.message)
            self._show("summary")
            self.query_one("#finish", Button).focus()  # Never auto-start.
            self._clear_secrets()
        except Exception as error:
            self._error(error)
        finally:
            self.busy = False

    def _clear_secrets(self) -> None:
        self.pending.clear()
        for name in SECRET_FIELDS:
            self.query_one(f"#secret-{name}", Input).value = ""

    def action_cancel(self) -> None:
        if not self.busy:
            self._clear_secrets()
            self.dismiss(self.saved)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.busy:
            return
        try:
            action = event.button.id
            if action in {"exit-welcome", "cancel-form", "cancel-review", "finish"}:
                self.action_cancel()
            elif action in {"configure", "back-form"}:
                self._show("form")
                self.query_one("#review-form", Button).focus()
            elif action in {"existing-review", "review-form"}:
                self._review(existing=action == "existing-review")
            elif action == "save":
                self.save_config()
        except Exception as error:
            self._error(error)


class SetupApp(App[None]):
    TITLE = "SI First-run Setup"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, service: SetupService) -> None:
        super().__init__()
        self.service = service

    def on_mount(self) -> None:
        self.push_screen(SetupScreen(self.service), lambda saved: self.exit())
