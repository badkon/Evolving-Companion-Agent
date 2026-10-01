"""Offline localhost OneBot round trip / replay / reconnect / shutdown smoke."""

import asyncio
import json

from websockets.asyncio.server import ServerConnection, serve

from evolving_companion.qq_adapter import QQPrivateChatAdapter
from evolving_companion.qq_transport import OneBotWebSocketTransport


class FakeConversation:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, text: str) -> str:
        self.calls.append(text)
        return "早上好"


async def run_smoke() -> None:
    core = FakeConversation()
    connections = 0
    finished = asyncio.Event()
    actions: list[dict[str, object]] = []
    event = dict(
        post_type="message",
        message_type="private",
        sub_type="friend",
        user_id=101,
        message_id=1,
        raw_message="早上好",
    )

    async def server(ws: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        await ws.send(json.dumps(event))
        if connections == 1:
            action = json.loads(await ws.recv())
            assert action["action"] == "send_private_msg"
            assert action["params"] == {
                "user_id": 101,
                "message": "早上好",
                "auto_escape": True,
            }
            assert isinstance(action["echo"], str) and action["echo"]
            actions.append(action)
            await ws.send(json.dumps(dict(echo=action["echo"], status="ok", retcode=0)))
            await ws.send(json.dumps(event))
            try:
                await asyncio.wait_for(ws.recv(), 0.1)
            except TimeoutError:
                print("Round trip + duplicate passed")
            else:
                raise AssertionError("Duplicate produced a second action")
            await ws.close()
        else:
            try:
                await asyncio.wait_for(ws.recv(), 0.1)
            except TimeoutError:
                print("Reconnect + replay dedup passed")
            else:
                raise AssertionError("Replay produced an action")
            finished.set()
            await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        transport = OneBotWebSocketTransport(
            QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202),
            onebot_ws_url=f"ws://127.0.0.1:{port}/",
            reconnect_interval=0.05,
        )
        task = asyncio.create_task(transport.run())
        try:
            await asyncio.wait_for(finished.wait(), 5)
            assert transport.last_delivery_result is not None
            assert transport.last_delivery_result.status == "sent"
        finally:
            await transport.close()
            await asyncio.wait_for(task, 3)
        assert core.calls == ["早上好"] and len(actions) == 1
        assert connections == 2 and transport.pending_count == 0
        print("Graceful shutdown passed; no DB / LLM / QQ / SnowLuma used")


if __name__ == "__main__":
    asyncio.run(run_smoke())
