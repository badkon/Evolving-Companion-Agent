"""Explicit, bounded operator probes. Never construct Conversation or write memory."""

import asyncio
import json
import logging
from uuid import uuid4
from urllib.parse import urlsplit

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
    APIStatusError,
)
from websockets.asyncio.client import connect

from evolving_companion.config_env import OperationResult
from evolving_companion.llm import LLMClient
from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
    MemoryProviderError,
)


async def probe_onebot(values: dict[str, str]) -> OperationResult:
    url = urlsplit(values.get("SI_ONEBOT_WS_URL", ""))
    if (
        url.scheme not in {"ws", "wss"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        return OperationResult(False, "WebSocket 地址格式无效。")
    _ = url.port
    token = values.get("SI_ONEBOT_ACCESS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    logger = logging.getLogger("si.web_setup.onebot")
    logger.setLevel(logging.WARNING)
    connected = False
    try:
        async with asyncio.timeout(12):
            async with connect(
                values["SI_ONEBOT_WS_URL"],
                additional_headers=headers,
                proxy=None,
                open_timeout=5,
                close_timeout=1,
                logger=logger,
                max_size=65536,
            ) as socket:
                connected = True

                async def action(name):
                    echo = uuid4().hex
                    await socket.send(
                        json.dumps({"action": name, "params": {}, "echo": echo})
                    )
                    async with asyncio.timeout(4):
                        async for frame in socket:
                            result = json.loads(frame)
                            if isinstance(result, dict) and result.get("echo") == echo:
                                if (
                                    result.get("status") != "ok"
                                    or result.get("retcode") != 0
                                ):
                                    raise ValueError("OneBot action failed")
                                return result["data"]
                    raise ValueError("Missing response")

                status = await action("get_status")
                login = await action("get_login_info")
                matches = str(login.get("user_id")) == values.get("SI_QQ_BOT_USER_ID")
                online = status.get("online") is True and status.get("good") is True
                if online and matches:
                    return OperationResult(
                        True,
                        "WebSocket / OneBot 已响应，QQ 在线且账号匹配；尚未验证 allowlist 私聊收发。",
                    )
                return OperationResult(
                    False,
                    "WebSocket / OneBot 已响应；QQ 未在线或账号不匹配，不能宣称聊天就绪。",
                )
    except Exception as error:
        prefix = (
            "WebSocket 已连接；OneBot / QQ 状态未确认"
            if connected
            else "WebSocket 连接失败"
        )
        return OperationResult(False, f"{prefix}（{type(error).__name__}）。")


def test_connection(kind: str, values: dict[str, str]) -> OperationResult:
    try:
        if kind == "qq":
            if values.get("SI_CHAT_TRANSPORT") == "none":
                return OperationResult(False, "当前无聊天连接；未执行 QQ 测试。")
            return asyncio.run(probe_onebot(values))
        if kind == "llm":
            client = LLMClient(
                environment=values, timeout=10, max_retries=0, max_output_tokens=16
            )
            try:
                # A real minimal completion, using the production adapter and model.
                client.complete(
                    [{"role": "user", "content": "连接测试，请只回复 OK。"}]
                )
            finally:
                client.close()
        elif kind == "embedding":
            client = APIEmbeddingProvider(
                provider_id=values["SI_MEMORY_EMBEDDING_API_ID"],
                model_id=values["SI_MEMORY_EMBEDDING_MODEL"],
                dimension=int(values["SI_MEMORY_EMBEDDING_DIMENSION"]),
                endpoint=values["SI_MEMORY_EMBEDDING_API_URL"],
                api_key=values["SI_MEMORY_EMBEDDING_API_KEY"],
                timeout=10,
            )
            try:
                client.embed_texts(["连接测试"])
            finally:
                client.close()
        elif kind == "reranker":
            client = APIRerankerProvider(
                model_id=values["SI_MEMORY_RERANKER_MODEL"],
                endpoint=values["SI_MEMORY_RERANKER_API_URL"],
                api_key=values["SI_MEMORY_RERANKER_API_KEY"],
                timeout=10,
            )
            try:
                client.score("连接测试", ["连接测试文本"])
            finally:
                client.close()
        else:
            raise ValueError("Unknown test")
        return OperationResult(
            True, "本次最小 API 请求成功；不代表持续可用，也未执行完整聊天。"
        )
    except AuthenticationError:
        return OperationResult(False, "认证失败，请检查密钥与账号权限。")
    except RateLimitError:
        return OperationResult(False, "服务限流或额度不足，请检查服务商账户。")
    except (APITimeoutError, TimeoutError):
        return OperationResult(False, "连接测试超时。")
    except APIConnectionError:
        return OperationResult(False, "无法连接 API，请检查地址、网络与 TLS。")
    except APIStatusError as error:
        return OperationResult(
            False,
            f"API 请求失败（HTTP {error.status_code}）；检查模型、权限和服务状态。",
        )
    except MemoryProviderError as error:
        # Provider errors are already typed/sanitized; never return payloads.
        messages = {
            "memory API request timed out": "Memory API 超时。",
            "memory API returned HTTP 401": "Memory API 认证失败。",
            "memory API returned HTTP 403": "Memory API 权限不足。",
            "memory API returned HTTP 429": "Memory API 限流或额度不足。",
            "memory API returned HTTP 404": "Memory API 模型或接口不存在。",
            "memory API request failed": "Memory API 网络连接失败。",
        }
        return OperationResult(
            False,
            messages.get(
                str(error), "Memory API 响应无效或请求失败；检查模型、维度与服务状态。"
            ),
        )
    except Exception as error:
        return OperationResult(
            False, f"连接测试失败（{type(error).__name__}）；检查配置。"
        )
