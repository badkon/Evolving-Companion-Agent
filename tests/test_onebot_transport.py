import asyncio
import json
import logging
import threading
from collections.abc import Callable

import pytest
from websockets.asyncio.server import ServerConnection, serve

from evolving_companion.qq_adapter import QQPrivateChatAdapter
from evolving_companion.qq_transport import OneBotWebSocketTransport


class FakeConversation:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.history: list[str] = []
        self.fail = fail

    def send(self, text: str) -> str:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("private provider details")
        self.history.append(text)
        return "早上好。[CQ:face,id=1]"


def event(message_id: int = 1) -> dict[str, object]:
    return dict(
        post_type="message",
        message_type="private",
        sub_type="friend",
        user_id=101,
        message_id=message_id,
        raw_message="早上好",
    )


async def until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_round_trip_filters_echo_and_secret_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        core = FakeConversation()
        actions: list[dict[str, object]] = []
        release = asyncio.Event()

        async def server(ws: ServerConnection) -> None:
            assert ws.request is not None
            assert ws.request.headers["Authorization"] == "Bearer fake-test-token"
            for payload in (
                "not-json",
                "[]",
                "{}",
                json.dumps(event() | {"message_type": "group"}),
                json.dumps(event() | {"user_id": 303}),
                json.dumps(event() | {"user_id": 202}),
            ):
                await ws.send(payload)
            await ws.send(json.dumps(event()))
            action = json.loads(await ws.recv())
            actions.append(action)
            assert action["action"] == "send_private_msg"
            assert action["params"] == {
                "user_id": 101,
                "message": "早上好。[CQ:face,id=1]",
                "auto_escape": True,
            }
            await ws.send(json.dumps({"echo": "wrong", "status": "ok", "retcode": 0}))
            await asyncio.sleep(0.02)
            assert transport.pending_count == 1
            assert transport.last_delivery_result is None
            await ws.send(
                json.dumps(
                    {
                        "echo": action["echo"],
                        "status": "ok",
                        "retcode": 0,
                        "post_type": "message",
                    }
                )
            )
            await ws.send(json.dumps(event()))
            await release.wait()

        # Silence only the fake server's protocol DEBUG; client suppresses its own.
        server_logger = logging.getLogger("fake_onebot_server")
        server_logger.setLevel(logging.WARNING)
        async with serve(server, "127.0.0.1", 0, logger=server_logger) as listener:
            port = listener.sockets[0].getsockname()[1]
            transport = OneBotWebSocketTransport(
                QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202),
                onebot_ws_url=f"ws://127.0.0.1:{port}/",
                onebot_access_token="fake-test-token",
                max_reconnects=0,
            )
            task = asyncio.create_task(transport.run())
            try:
                await until(lambda: transport.last_delivery_result is not None)
                assert transport.last_delivery_result is not None
                assert transport.last_delivery_result.status == "sent"
                assert transport.last_delivery_result.echo == actions[0]["echo"]
                await until(lambda: "message_already_attempted" in caplog.text)
                assert core.calls == ["早上好"]
            finally:
                await transport.close()
                release.set()
                await asyncio.wait_for(task, 3)
            assert transport.pending_count == 0

    asyncio.run(scenario())
    assert "fake-test-token" not in caplog.text
    assert "早上好" not in caplog.text


def test_reconnect_budget_and_close_during_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evolving_companion.qq_transport as module

    async def scenario() -> None:
        attempts = 0

        async def unavailable(*args: object, **kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            raise OSError("secret endpoint details")

        monkeypatch.setattr(module, "connect", unavailable)
        adapter = QQPrivateChatAdapter(
            FakeConversation(), allowed_user_ids={101}, bot_user_id=202
        )
        transport = OneBotWebSocketTransport(
            adapter,
            onebot_ws_url="ws://127.0.0.1:3001/",
            reconnect_interval=0,
            max_reconnects=2,
        )
        await transport.run()
        assert attempts == 3
        attempts = 0
        transport = OneBotWebSocketTransport(
            adapter,
            onebot_ws_url="ws://127.0.0.1:3001/",
            reconnect_interval=5,
        )
        task = asyncio.create_task(transport.run())
        await until(lambda: attempts == 1)
        await transport.close()
        await asyncio.wait_for(task, 0.5)
        assert attempts == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode", ["api_failure", "timeout", "disconnect", "core_failure"]
)
def test_failure_never_reprocesses_core(mode: str) -> None:
    async def scenario() -> None:
        core = FakeConversation(fail=mode == "core_failure")
        actions: list[object] = []
        release = asyncio.Event()

        async def server(ws: ServerConnection) -> None:
            await ws.send(json.dumps(event()))
            if mode == "core_failure":
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.05)
                release.set()
                await ws.wait_closed()
                return
            action = json.loads(await ws.recv())
            actions.append(action)
            if mode == "disconnect":
                await ws.close()
                return
            if mode == "api_failure":
                await ws.send(
                    json.dumps(
                        {
                            "echo": action["echo"],
                            "status": "failed",
                            "retcode": 100,
                            "wording": "private error details",
                        }
                    )
                )
            await ws.send(json.dumps(event()))
            await ws.wait_closed()

        async with serve(server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            transport = OneBotWebSocketTransport(
                QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202),
                onebot_ws_url=f"ws://127.0.0.1:{port}/",
                max_reconnects=0,
                action_timeout=0.04,
            )
            task = asyncio.create_task(transport.run())
            try:
                if mode == "core_failure":
                    await asyncio.wait_for(release.wait(), 3)
                    assert actions == [] and transport.last_delivery_result is None
                    assert core.history == []
                else:
                    await until(lambda: transport.last_delivery_result is not None)
                    assert transport.last_delivery_result is not None
                    assert transport.last_delivery_result.status == "failed"
                    assert len(actions) == 1
                    assert core.history == ["早上好"]
                assert core.calls == ["早上好"]
            finally:
                await transport.close()
                await asyncio.wait_for(task, 3)

    asyncio.run(scenario())


def test_reconnect_keeps_dedup_and_close_does_not_reconnect() -> None:
    async def scenario() -> None:
        core = FakeConversation()
        connections = 0
        actions: list[object] = []
        replay_checked = asyncio.Event()

        async def server(ws: ServerConnection) -> None:
            nonlocal connections
            connections += 1
            await ws.send(json.dumps(event()))
            if connections == 1:
                action = json.loads(await ws.recv())
                actions.append(action)
                await ws.send(
                    json.dumps({"echo": action["echo"], "status": "ok", "retcode": 0})
                )
                await ws.close()
            else:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.05)
                replay_checked.set()
                await ws.wait_closed()

        async with serve(server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            transport = OneBotWebSocketTransport(
                QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202),
                onebot_ws_url=f"ws://127.0.0.1:{port}/",
                reconnect_interval=0.01,
                max_reconnects=2,
            )
            task = asyncio.create_task(transport.run())
            try:
                await asyncio.wait_for(replay_checked.wait(), 3)
                assert core.calls == ["早上好"] and len(actions) == 1
            finally:
                await transport.close()
                await asyncio.wait_for(task, 3)
            await asyncio.sleep(0.03)
            assert connections == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_run", [False, True])
def test_shutdown_waits_for_active_core_without_sending(cancel_run: bool) -> None:
    async def scenario() -> None:
        started, release = threading.Event(), threading.Event()

        class SlowCore(FakeConversation):
            def send(self, text: str) -> str:
                started.set()
                release.wait(3)
                return super().send(text)

        core = SlowCore()

        async def server(ws: ServerConnection) -> None:
            await ws.send(json.dumps(event()))
            await ws.wait_closed()

        async with serve(server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            transport = OneBotWebSocketTransport(
                QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202),
                onebot_ws_url=f"ws://127.0.0.1:{port}/",
            )
            task = asyncio.create_task(transport.run())
            await until(started.is_set)
            if cancel_run:
                task.cancel()
                closer = task
            else:
                closer = asyncio.create_task(transport.close())
            await asyncio.sleep(0.02)
            assert not closer.done()
            release.set()
            if cancel_run:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                await asyncio.wait_for(closer, 3)
                await asyncio.wait_for(task, 3)
            assert core.history == ["早上好"]
            assert transport.pending_count == 0

    asyncio.run(scenario())


def test_entry_missing_routing_does_not_initialize_core(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evolving_companion import qq_cli

    monkeypatch.setattr(qq_cli, "load_local_env", lambda: True)
    monkeypatch.delenv("SI_QQ_BOT_USER_ID", raising=False)
    monkeypatch.delenv("SI_QQ_ALLOWED_USER_IDS", raising=False)

    def forbidden() -> None:
        raise AssertionError("Core must not be initialized")

    monkeypatch.setattr(qq_cli, "create_conversation", forbidden)
    qq_cli.main()
    assert "Configure SI_QQ_BOT_USER_ID" in capsys.readouterr().out


def test_entry_injects_env_config_without_reading_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evolving_companion import qq_cli

    monkeypatch.setattr(qq_cli, "load_local_env", lambda: True)
    monkeypatch.setenv("SI_QQ_BOT_USER_ID", "202")
    monkeypatch.setenv("SI_QQ_ALLOWED_USER_IDS", "101")
    monkeypatch.setenv("SI_ONEBOT_WS_URL", "ws://127.0.0.1:12345/")
    monkeypatch.setenv("SI_ONEBOT_ACCESS_TOKEN", "fake-env-token")
    core = FakeConversation()
    monkeypatch.setattr(qq_cli, "create_conversation", lambda: core)
    captured: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, adapter: QQPrivateChatAdapter, **config: object) -> None:
            captured.update(config)
            self.adapter = adapter

        async def run(self) -> None:
            assert self.adapter.handle_event(event()).status == "handled"

    monkeypatch.setattr(qq_cli, "OneBotWebSocketTransport", FakeTransport)
    qq_cli.main()
    assert captured == {
        "onebot_ws_url": "ws://127.0.0.1:12345/",
        "onebot_access_token": "fake-env-token",
    }
    assert core.calls == ["早上好"]
