"""Minimal forward OneBot v11 WebSocket transport, independent of Character data."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from evolving_companion.qq_adapter import AdapterHandlingResult, QQPrivateChatAdapter

logger = logging.getLogger(__name__)
protocol_logger = logging.getLogger("evolving_companion.onebot_protocol")
# Protocol DEBUG dumps handshake headers; never enable it for this connection.
protocol_logger.setLevel(logging.WARNING)


@dataclass(frozen=True)
class DeliveryResult:
    echo: str
    status: Literal["sent", "failed"]
    reason: str


class OneBotWebSocketTransport:
    def __init__(
        self,
        adapter: QQPrivateChatAdapter,
        *,
        onebot_ws_url: str,
        onebot_access_token: str | None = None,
        reconnect_interval: float = 5,
        max_reconnects: int = 3,
        action_timeout: float = 30,
    ) -> None:
        url = urlsplit(onebot_ws_url)
        if (
            url.scheme not in {"ws", "wss"}
            or not url.hostname
            or url.username
            or url.query
        ):
            raise ValueError(
                "Use a ws/wss endpoint without credentials or query secrets"
            )
        if reconnect_interval < 0 or max_reconnects < 0 or action_timeout <= 0:
            raise ValueError("Invalid reconnect or timeout configuration")
        self._adapter = adapter
        self._url = onebot_ws_url
        self._headers = (
            {"Authorization": f"Bearer {onebot_access_token}"}
            if onebot_access_token
            else None
        )
        self._interval = reconnect_interval
        self._max_reconnects = max_reconnects
        self._timeout = action_timeout
        self._socket: ClientConnection | None = None
        self._closed = asyncio.Event()
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self._processing: asyncio.Task[AdapterHandlingResult] | None = None
        self._running = False
        self.last_delivery_result: DeliveryResult | None = None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def connect(self) -> None:
        if self._closed.is_set():
            raise RuntimeError("Transport is closed")
        if self._socket is not None:
            return
        logger.info("OneBot connecting")
        socket = await connect(
            self._url,
            additional_headers=self._headers,
            proxy=None,
            open_timeout=10,
            close_timeout=5,
            logger=protocol_logger,
        )
        if self._closed.is_set():
            await socket.close()
            return
        self._socket = socket
        logger.info("OneBot connected")

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("Transport already running")
        self._running = True
        reconnects = 0
        try:
            while not self._closed.is_set():
                try:
                    await self.connect()
                    if self._socket is not None:
                        async for frame in self._socket:
                            if self._closed.is_set():
                                break
                            await self._handle_frame(frame)
                except (OSError, WebSocketException, TimeoutError):
                    # Exceptions / endpoint / server wording may contain secrets.
                    logger.warning("OneBot connection unavailable")
                finally:
                    await self._disconnect()
                if self._closed.is_set() or reconnects >= self._max_reconnects:
                    break
                reconnects += 1
                logger.info(
                    "OneBot reconnecting (%s/%s)", reconnects, self._max_reconnects
                )
                try:
                    await asyncio.wait_for(self._closed.wait(), self._interval)
                except TimeoutError:
                    pass
        finally:
            await self.close()
            self._running = False

    async def _handle_frame(self, frame: str | bytes) -> None:
        if not isinstance(frame, str):
            logger.debug("OneBot ignored binary frame")
            return
        try:
            payload = json.loads(frame)
        except (ValueError, RecursionError):
            logger.debug("OneBot ignored invalid JSON")
            return
        if not isinstance(payload, dict):
            logger.debug("OneBot ignored non-object frame")
            return
        # Response-shaped frames never enter Adapter, even if they have post_type.
        if "echo" in payload or "retcode" in payload or "status" in payload:
            echo = payload.get("echo")
            if not isinstance(echo, str) or echo not in self._pending:
                logger.debug("OneBot ignored unmatched API response")
                return
            success = (
                payload.get("status") == "ok"
                and type(payload.get("retcode")) is int
                and payload["retcode"] == 0
            )
            self._finish_delivery(
                echo, success, "api_success" if success else "api_failure"
            )
            return
        if "post_type" not in payload:
            logger.debug("OneBot ignored malformed event frame")
            return
        self._processing = asyncio.create_task(
            asyncio.to_thread(self._adapter.handle_event, payload)
        )
        # Only one Core call at a time; cancellation doesn't abandon an active turn.
        result = await asyncio.shield(self._processing)
        self._processing = None
        if result.status == "failed":
            logger.error("OneBot adapter failure")
            return
        if result.status != "handled" or result.response is None:
            logger.debug("OneBot ignored event: %s", result.reason)
            return
        if self._closed.is_set():
            logger.error("OneBot send failure: shutdown after Core completion")
            return
        echo = str(uuid4())
        self._pending[echo] = asyncio.get_running_loop().call_later(
            self._timeout, self._finish_delivery, echo, False, "timeout"
        )
        action = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(result.response.recipient_id),
                "message": result.response.text,
                "auto_escape": True,
            },
            "echo": echo,
        }
        try:
            assert self._socket is not None
            await self._socket.send(json.dumps(action, ensure_ascii=False))
        except (OSError, WebSocketException):
            self._finish_delivery(echo, False, "socket_send_failure")
            raise

    def _finish_delivery(self, echo: str, success: bool, reason: str) -> None:
        timer = self._pending.pop(echo, None)
        if timer is None:
            return
        timer.cancel()
        self.last_delivery_result = DeliveryResult(
            echo, "sent" if success else "failed", reason
        )
        if not success:
            logger.error("OneBot send failure: %s", reason)

    async def _disconnect(self) -> None:
        socket, self._socket = self._socket, None
        for echo in tuple(self._pending):
            self._finish_delivery(echo, False, "disconnected")
        if socket is not None:
            await socket.close()
            logger.info("OneBot disconnected")

    async def close(self) -> None:
        self._closed.set()
        await self._disconnect()
        if self._processing is not None:
            await asyncio.shield(self._processing)
            self._processing = None


if __name__ == "__main__":
    from evolving_companion.qq_cli import main

    main()
