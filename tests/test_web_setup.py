"""Temporary fixtures only; no production DB, credentials or external APIs."""

import asyncio
from copy import deepcopy
from pathlib import Path
import shutil
from subprocess import CompletedProcess
from threading import Event, Thread

import pytest
import yaml
from starlette.testclient import TestClient

from evolving_companion import connection_tests, web_setup_services
from evolving_companion.config_env import EnvEditError, OperationResult
from evolving_companion.web_setup import create_app, WebSetupServer
from evolving_companion.web_setup_services import WebSetupService


@pytest.fixture
def service(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in (
        "config/si.env.example",
        "data/characters/si_001.yaml",
        "data/worlds/si_world.yaml",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    (tmp_path / "runtime").mkdir()
    return WebSetupService(tmp_path / "config/si.env", tmp_path, {})


def client(service):
    return TestClient(
        create_app(service, "fake-access"),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer fake-access"},
    )


def test_html_auth_origin_and_safe_rendering(service):
    service.env.path.write_text("DEEPSEEK_API_KEY=fake-secret\n")
    with client(service) as web:
        home = web.get("/")
        assert home.status_code == 200 and "SI 配置" in home.text
        assert "fake-secret" not in home.text and "fake-access" not in home.text
        state = web.get("/api/state")
        assert (
            "fake-secret" not in state.text
            and state.json()["secrets"]["DEEPSEEK_API_KEY"]
        )
        assert state.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in state.headers["content-security-policy"]
        assert (
            web.get("/api/state", headers={"Authorization": "bad"}).status_code == 401
        )
        assert (
            web.post(
                "/api/save", json={}, headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        assert (
            web.get("/api/state", headers={"Host": "evil.invalid"}).status_code == 400
        )
        assert web.post("/api/save", content="hello").status_code == 415
        assert (
            web.post(
                "/api/save",
                content=b"x" * 262145,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        script = web.get("/static/setup.js").text
        assert "innerHTML" not in script and "textContent" in script


def test_keep_replace_clear_conflict_and_os_priority(service):
    service.env.path.write_text("DEEPSEEK_API_KEY=fake-old\nCUSTOM=preserved\n")
    snap = service.snapshot()
    service.save({"version": snap["version"], "values": {"SI_LLM_MODEL": "test-model"}})
    assert service.env.read()["DEEPSEEK_API_KEY"] == "fake-old"
    assert service.env.read()["CUSTOM"] == "preserved"
    with pytest.raises(EnvEditError):
        service.save({"version": snap["version"]})
    # Snapshot backups must not overwrite one another, even within a second.
    for action in ({"mode": "replace", "value": "fake-new"}, {"mode": "clear"}):
        service.save(
            {
                "version": service.snapshot()["version"],
                "secrets": {"DEEPSEEK_API_KEY": action},
            }
        )
    assert service.env.read()["DEEPSEEK_API_KEY"] == ""
    service.environment["DEEPSEEK_API_KEY"] = "fake-os"
    assert service.effective()["DEEPSEEK_API_KEY"] == "fake-os"
    assert "fake-os" not in str(service.snapshot())
    for replacement in ("", "********", "[REDACTED]"):
        with pytest.raises(ValueError):
            service.updates(
                {
                    "secrets": {
                        "DEEPSEEK_API_KEY": {"mode": "replace", "value": replacement}
                    }
                }
            )


def test_character_roundtrip_identity_unknown_and_yaml_validation(service):
    old = yaml.safe_load(service.seed._text())
    snapshot = service.snapshot()
    service.save_character(
        {
            "version": snapshot["character_version"],
            "character": {
                "working_name": "测试称呼",
                "likes": "读书\n音乐",
                "strangers": "<script>不是 HTML</script>",
            },
        }
    )
    data = yaml.safe_load(service.seed._text())
    assert data["identity"]["internal_id"] == old["identity"]["internal_id"]
    assert data["knowledge_boundaries"] == old["knowledge_boundaries"]
    assert data["seed_preferences"]["likes"] == ["读书", "音乐"]
    for key in (
        "internal_id",
        "development_id",
        "continuity_generation",
        "personal_name",
        "identity_stage",
    ):
        invalid = deepcopy(data)
        invalid["identity"][key] = "illegal"
        with pytest.raises(ValueError):
            service.save_character(
                {
                    "version": service.snapshot()["character_version"],
                    "yaml": yaml.safe_dump(invalid),
                }
            )
    for text in (
        "!!python/object/apply:os.system ['bad']",
        "identity: [",
        yaml.safe_dump(data | {"future_unknown": "preserve"}),
    ):
        before = service.seed._text()
        with pytest.raises(Exception):
            service.save_character(
                {"version": service.snapshot()["character_version"], "yaml": text}
            )
        assert before == service.seed._text()
    with pytest.raises(EnvEditError):
        service.save_character(
            {"version": snapshot["character_version"], "character": {}}
        )


def test_validation_uses_runtime_check_without_mutating_environment(
    service, monkeypatch
):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, "Transport OK", "private-error")

    monkeypatch.setattr(web_setup_services.subprocess, "run", run)
    result = service.validate(
        {
            "secrets": {
                "DEEPSEEK_API_KEY": {"mode": "replace", "value": "fake-ds"},
                "SILICONFLOW_API_KEY": {"mode": "replace", "value": "fake-sf"},
            }
        }
    )
    assert result.ok and "private" not in result.message
    assert "evolving_companion.runtime_check" in calls[0][0]
    assert calls[0][1]["env"]["SI_MEMORY_EMBEDDING_API_KEY"] == "fake-sf"
    assert not service.env.path.exists()
    assert not (service.project / "runtime/si_001.db").exists()


def test_safe_failure_does_not_expose_input(service, monkeypatch):
    def fail(payload):
        raise ValueError("fake-private-token <script>")

    monkeypatch.setattr(service, "save", fail)
    with client(service) as web:
        response = web.post("/api/save", json={})
        assert response.status_code == 400
        assert "ValueError" in response.text
        assert (
            "fake-private-token" not in response.text
            and "<script>" not in response.text
        )


def test_real_loopback_server_stops_and_drains_inflight_save(service, monkeypatch):
    import httpx

    entered, release = Event(), Event()

    def save(payload):
        entered.set()
        assert release.wait(5)
        return OperationResult(True, "saved")

    monkeypatch.setattr(service, "save", save)
    server = WebSetupServer(service)
    server.start()
    responses = []

    def request():
        with httpx.Client(trust_env=False) as web:
            responses.append(
                web.post(
                    server.url.split("#")[0] + "api/save",
                    json={},
                    headers={"Authorization": f"Bearer {server.token}"},
                )
            )

    request_thread = Thread(target=request)
    request_thread.start()
    assert entered.wait(5)
    stop_thread = Thread(target=server.stop)
    stop_thread.start()
    assert stop_thread.is_alive()
    release.set()
    request_thread.join(5)
    stop_thread.join(5)
    assert not server.thread.is_alive() and not stop_thread.is_alive()
    assert responses[0].json()["ok"]


def test_connection_probes_use_adapters_and_close(service, monkeypatch):
    calls = []

    class Fake:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def complete(self, messages):
            calls.append(messages)
            return "OK"

        def embed_texts(self, texts):
            calls.append(texts)

        def score(self, query, texts):
            calls.append((query, texts))

        def close(self):
            calls.append("close")

    for name in ("LLMClient", "APIEmbeddingProvider", "APIRerankerProvider"):
        monkeypatch.setattr(connection_tests, name, Fake)
    for kind in ("llm", "embedding", "reranker"):
        assert connection_tests.test_connection(kind, service.effective()).ok
        assert calls[-1] == "close"
    assert not connection_tests.test_connection("qq", service.effective()).ok


def test_onebot_probe_checks_login_not_just_websocket(monkeypatch):
    class Socket:
        async def send(self, text):
            import json

            self.request = json.loads(text)

        def __aiter__(self):
            return self

        async def __anext__(self):
            import json

            data = (
                {"online": True, "good": True}
                if self.request["action"] == "get_status"
                else {"user_id": 123}
            )
            return json.dumps(
                {
                    "echo": self.request["echo"],
                    "status": "ok",
                    "retcode": 0,
                    "data": data,
                }
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(connection_tests, "connect", lambda *args, **kwargs: Socket())
    values = {"SI_ONEBOT_WS_URL": "ws://127.0.0.1:3001", "SI_QQ_BOT_USER_ID": "123"}
    assert asyncio.run(connection_tests.probe_onebot(values)).ok
    values["SI_QQ_BOT_USER_ID"] = "456"
    assert not asyncio.run(connection_tests.probe_onebot(values)).ok


@pytest.mark.parametrize("mode", ["none", "qq"])
def test_web_save_manager_fresh_start_existing_runtime_and_qq_reply(
    service, monkeypatch, mode
):
    """The only replacements are process execution and external LLM/WS I/O."""
    import json
    import os
    import sqlite3
    import sys
    from contextlib import closing
    from evolving_companion import runtime, runtime_config, server, qq_transport
    from evolving_companion.manager_services import ManagerService
    from evolving_companion.runtime_control import RuntimeStatus

    replies = []
    initialized = []

    class FakeLLM:
        def complete(self, messages):
            if "Memory Extraction" in messages[0]["content"]:
                return '{"candidates":[]}'
            return "你好，测试收到。"

    monkeypatch.setattr(runtime, "LLMClient", FakeLLM)
    monkeypatch.setattr(
        runtime, "__file__", str(service.project / "src/evolving_companion/runtime.py")
    )
    monkeypatch.setattr(runtime_config, "PROJECT_ROOT", service.project)

    async def idle():
        initialized.append("none")

    monkeypatch.setattr(runtime, "wait_for_shutdown", idle)

    class FakeSocket:
        async def send(self, text):
            replies.append(json.loads(text))

        async def close(self):
            pass

    async def fake_wire(self):
        initialized.append("qq")
        self._socket = FakeSocket()
        await self._handle_frame(
            json.dumps(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "user_id": 101,
                    "message_id": 1,
                    "raw_message": "你好",
                }
            )
        )
        assert replies
        await self._handle_frame(
            json.dumps({"echo": replies[-1]["echo"], "status": "ok", "retcode": 0})
        )
        await self.close()
        raise KeyboardInterrupt  # Exercise existing graceful QQ exit path.

    monkeypatch.setattr(qq_transport.OneBotWebSocketTransport, "run", fake_wire)
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "SYSTEMROOT")
        if key in os.environ
    }
    service.environment = environment

    class Controller:
        def start(self):
            with monkeypatch.context() as child:
                for key in tuple(os.environ):
                    child.delenv(key)
                for key, value in environment.items():
                    child.setenv(key, value)
                child.chdir(service.project)
                child.setattr(
                    sys, "argv", ["server", "--env-file", str(service.env.path)]
                )
                return OperationResult(server.main() == 0, "fake process boundary")

        def stop(self):
            return OperationResult(True, "stopped")

        def restart(self):
            return self.start()

        def status(self):
            return RuntimeStatus("Stopped", "fake")

        def logs(self):
            return OperationResult(True, "fake")

    manager = ManagerService(
        service.env.path,
        project_root=service.project,
        environment=environment,
        controller=Controller(),
    )
    # Manager existed before the Web save; no cached dotenv settings may win.
    assert not manager.start().ok
    with client(service) as web:
        state = web.get("/api/state").json()
        assert web.post(
            "/api/save",
            json={
                "version": state["version"],
                "values": {
                    "SI_CHAT_TRANSPORT": mode,
                    "SI_QQ_BOT_USER_ID": "202",
                    "SI_QQ_ALLOWED_USER_IDS": "101",
                },
                "secrets": {
                    key: {"mode": "replace", "value": "fake-key"}
                    for key in ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY")
                },
            },
        ).json()["ok"]
    assert manager.start().ok
    assert initialized == [mode]
    with closing(sqlite3.connect(service.project / "runtime/si_001.db")) as database:
        assert database.execute("PRAGMA quick_check").fetchone() == ("ok",)
        rows = database.execute(
            "SELECT role,content FROM archive_messages ORDER BY rowid"
        ).fetchall()
    if mode == "qq":
        assert replies[-1]["action"] == "send_private_msg"
        assert replies[-1]["params"] == {
            "user_id": 101,
            "message": "你好，测试收到。",
            "auto_escape": True,
        }
        assert rows == [("user", "你好"), ("assistant", "你好，测试收到。")]
    else:
        assert not replies and not rows


def test_invalid_config_save_preserves_original(service):
    for values in (
        {"SI_LLM_MODEL": ""},
        {"SI_LLM_API_URL": "https://user:secret@example.com"},
        {"SI_MEMORY_EMBEDDING_DIMENSION": "0"},
        {"SI_CHAT_TRANSPORT": "qq", "SI_QQ_ALLOWED_USER_IDS": "bad"},
    ):
        with pytest.raises(ValueError):
            service.save({"version": service.snapshot()["version"], "values": values})
        assert not service.env.path.exists()


def test_rejected_unknown_existing_seed_is_not_dropped(service):
    original = service.seed._text() + "future_field: keep-me\n"
    service.seed.path.write_text(original, encoding="utf-8")
    from evolving_companion.web_setup_services import revision

    with pytest.raises(ValueError):
        service.save_character(
            {"version": revision(original), "character": {"working_name": "测试"}}
        )
    assert service.seed._text() == original


def test_atomic_failure_preserves_seed_and_private_backup(service, monkeypatch):
    import evolving_companion.config_env as config_env

    before = service.seed._text()

    def denied(*args):
        raise PermissionError("fake-private-detail")

    monkeypatch.setattr(config_env.os, "replace", denied)
    with pytest.raises(PermissionError):
        service.save_character(
            {
                "version": service.snapshot()["character_version"],
                "character": {"working_name": "change"},
            }
        )
    assert service.seed._text() == before
    assert (
        list(service.seed.path.parent.glob("*.yaml.bak.*"))[0].read_text(
            encoding="utf-8"
        )
        == before
    )
    assert not list(service.seed.path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "message, expected",
    [
        ("memory API request timed out", "超时"),
        ("memory API returned HTTP 401", "认证"),
        ("memory API returned HTTP 429", "限流"),
        ("fake-sensitive-payload", "请求失败"),
    ],
)
def test_memory_probe_safe_classification(service, monkeypatch, message, expected):
    def fail(**kwargs):
        raise connection_tests.MemoryProviderError(message)

    monkeypatch.setattr(connection_tests, "APIEmbeddingProvider", fail)
    result = connection_tests.test_connection("embedding", service.effective())
    assert not result.ok and expected in result.message
    assert "fake-sensitive" not in result.message


def test_llm_endpoint_model_and_bounded_probe_use_selected_config(monkeypatch):
    from types import SimpleNamespace
    from evolving_companion import llm

    captured = []

    class FakeOpenAI:
        def __init__(self, **options):
            captured.append(options)
            self.chat = SimpleNamespace(completions=self)

        def create(self, **options):
            captured.append(options)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

        def close(self):
            captured.append("closed")

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "DefaultHttpxClient", lambda **kwargs: "fake-http")
    result = connection_tests.test_connection(
        "llm",
        {
            "DEEPSEEK_API_KEY": "fake-test",
            "SI_LLM_API_URL": "https://example.invalid/v1",
            "SI_LLM_MODEL": "selected-model",
        },
    )
    assert result.ok and captured[-1] == "closed"
    assert captured[0]["base_url"] == "https://example.invalid/v1"
    assert captured[0]["max_retries"] == 0 and captured[0]["timeout"] == 10
    assert captured[1]["model"] == "selected-model" and captured[1]["max_tokens"] == 16


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("auth", "认证"),
        ("rate", "限流"),
        ("timeout", "超时"),
        ("network", "无法连接"),
        ("status", "HTTP 404"),
    ],
)
def test_llm_error_classification_never_returns_payload(
    monkeypatch, caplog, kind, expected
):
    import httpx

    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(404, request=request)
    error = {
        "auth": connection_tests.AuthenticationError(
            "fake-private", response=response, body={"key": "fake-private"}
        ),
        "rate": connection_tests.RateLimitError(
            "fake-private", response=response, body=None
        ),
        "timeout": connection_tests.APITimeoutError(request),
        "network": connection_tests.APIConnectionError(request=request),
        "status": connection_tests.APIStatusError(
            "fake-private", response=response, body=None
        ),
    }[kind]
    closed = []

    class Fake:
        def __init__(self, **kwargs):
            pass

        def complete(self, messages):
            raise error

        def close(self):
            closed.append(True)

    monkeypatch.setattr(connection_tests, "LLMClient", Fake)
    result = connection_tests.test_connection("llm", {})
    assert not result.ok and expected in result.message and closed == [True]
    assert "fake-private" not in result.message + caplog.text


def test_existing_valid_seed_without_optional_life_context(service):
    data = yaml.safe_load(service.seed._text())
    data.pop("initial_life_context")
    service.seed.path.write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    snapshot = service.snapshot()
    assert "current_role" in snapshot["character"]
    service.save_character(
        {
            "version": snapshot["character_version"],
            "character": {"current_role": "学生"},
        }
    )
    saved = yaml.safe_load(service.seed._text())
    assert saved["identity"] == data["identity"]
    assert saved["initial_life_context"] == {"current_role": "学生"}


def test_two_concurrent_saves_one_revision_one_winner(service):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    gate = Barrier(2)
    version = service.snapshot()["version"]

    def save(model):
        gate.wait(timeout=3)
        try:
            return service.save(
                {"version": version, "values": {"SI_LLM_MODEL": model}}
            ).ok
        except EnvEditError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["model-a", "model-b"]))
    assert sorted(results) == [False, True]
    assert service.env.read()["SI_LLM_MODEL"] in {"model-a", "model-b"}


def test_no_path_traversal_or_arbitrary_file_write(service):
    sentinel = service.project / "private.txt"
    sentinel.write_text("do-not-change")
    with client(service) as web:
        for path, expected in (
            ("/static/%2e%2e/private.txt", 404),
            ("/static/%2e%2e/%2e%2e/config/si.env", 404),
            ("/api/private.txt", 405),
        ):
            response = web.get(path)
            assert (
                response.status_code == expected
                and "do-not-change" not in response.text
            )
        assert (
            web.post(
                "/api/save",
                json={
                    "version": service.snapshot()["version"],
                    "values": {"path": str(sentinel)},
                },
            ).status_code
            == 400
        )
    assert sentinel.read_text() == "do-not-change"


def test_web_thread_start_failure_closes_socket(service, monkeypatch):
    server = WebSetupServer(service)

    def fail():
        raise RuntimeError("failed start")

    monkeypatch.setattr(server.thread, "start", fail)
    with pytest.raises(RuntimeError):
        server.start()
    assert server.socket.fileno() == -1


def test_onebot_connection_failure_is_not_chat_ready(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("fake-token-in-url")

    monkeypatch.setattr(connection_tests, "connect", fail)
    result = asyncio.run(
        connection_tests.probe_onebot({"SI_ONEBOT_WS_URL": "ws://127.0.0.1:3001"})
    )
    assert not result.ok and "WebSocket 连接失败" in result.message
    assert "fake-token" not in result.message
