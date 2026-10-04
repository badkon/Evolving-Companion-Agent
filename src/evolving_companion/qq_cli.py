"""OneBot entry-point wiring; no SnowLuma process management."""

import asyncio
from contextlib import ExitStack
import logging
import os

from evolving_companion.local_env import load_local_env
from evolving_companion.runtime import create_conversation
from evolving_companion.qq_adapter import QQPrivateChatAdapter, _identifier
from evolving_companion.qq_transport import OneBotWebSocketTransport
from evolving_companion.media_input import create_vision_input


def main(*, load_environment: bool = True) -> int:
    with ExitStack() as resources:
        return _run(resources, load_environment=load_environment)


def _run(resources: ExitStack, *, load_environment: bool = True) -> int:
    logging.basicConfig(level=logging.INFO)
    configured = (
        load_local_env()
        if load_environment
        else bool(os.environ.get("DEEPSEEK_API_KEY"))
    )
    if not configured:
        print(
            "DEEPSEEK_API_KEY is not configured. Set it in the environment or create .env.local from .env.example."
        )
        return 78
    try:
        bot_id = os.environ["SI_QQ_BOT_USER_ID"]
        allowed = [
            item.strip()
            for item in os.environ["SI_QQ_ALLOWED_USER_IDS"].split(",")
            if item.strip()
        ]
        # Validate routing config before any production database initialization.
        _identifier(bot_id, user=True)
        for user_id in allowed:
            _identifier(user_id, user=True)
    except (KeyError, ValueError):
        print("Configure SI_QQ_BOT_USER_ID and comma-separated SI_QQ_ALLOWED_USER_IDS.")
        return 78
    try:
        adapter = QQPrivateChatAdapter(
            create_conversation(resources),
            allowed_user_ids=allowed,
            bot_user_id=bot_id,
            vision_input=create_vision_input(resources),
        )
        transport = OneBotWebSocketTransport(
            adapter,
            onebot_ws_url=os.environ.get("SI_ONEBOT_WS_URL", "ws://127.0.0.1:3001/"),
            onebot_access_token=os.environ.get("SI_ONEBOT_ACCESS_TOKEN") or None,
        )
        asyncio.run(transport.run())
    except KeyboardInterrupt:
        return 0
    except Exception:
        logging.getLogger(__name__).error(
            "OneBot startup/run failed; check local configuration"
        )
        return 1
    # A long-running transport exhausting reconnects is not a healthy service exit.
    return 1
