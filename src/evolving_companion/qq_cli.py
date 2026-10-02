"""OneBot entry-point wiring; no SnowLuma process management."""

import asyncio
from contextlib import ExitStack
import logging
import os
from pathlib import Path

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import SystemClock
from evolving_companion.conversation import Conversation
from evolving_companion.llm import LLMClient
from evolving_companion.local_env import load_local_env
from evolving_companion.memory_consolidation import (
    MemoryConsolidationJudge,
    MemoryConsolidationService,
)
from evolving_companion.memory_extraction import MemoryExtractor
from evolving_companion.memory_formation import MemoryFormationService
from evolving_companion.memory_recall import MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.memory_provider_config import create_memory_providers
from evolving_companion.qq_adapter import QQPrivateChatAdapter, _identifier
from evolving_companion.qq_transport import OneBotWebSocketTransport
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed


def create_conversation(resources: ExitStack) -> Conversation:
    root = Path(__file__).resolve().parents[2]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    store = SQLiteStore(root / "runtime/si_001.db")
    clock = SystemClock()
    for diagnostic in WorldEntityService(store, clock).initialize_seed_entities(
        load_world_seed(root / "data/worlds/si_world.yaml")
    ):
        logging.getLogger(__name__).warning("World migration: %s", diagnostic)
    embedding_provider, reranker_provider = create_memory_providers(resources)
    retriever = MemoryRetriever(store, embedding_provider)
    llm = LLMClient()
    return Conversation(
        llm,
        CharacterProjector().project(seed),
        store,
        memory_recall_service=MemoryRecallService(
            retriever, MemoryReranker(provider=reranker_provider)
        ),
        memory_formation_service=MemoryFormationService(
            MemoryExtractor(llm),
            store,
            MemoryConsolidationService(store, retriever, MemoryConsolidationJudge(llm)),
        ),
        character_state_service=CharacterStateService(store, clock),
        character_id=seed.identity.internal_id,
        character_timezone=seed.timezone,
        clock=clock,
        character_life_service=CharacterLifeService(
            store, seed.identity.internal_id, seed.initial_life_context, clock
        ),
    )


def main() -> None:
    with ExitStack() as resources:
        _run(resources)


def _run(resources: ExitStack) -> None:
    logging.basicConfig(level=logging.INFO)
    if not load_local_env():
        print(
            "DEEPSEEK_API_KEY is not configured. Set it in the environment or create .env.local from .env.example."
        )
        return
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
        return
    try:
        adapter = QQPrivateChatAdapter(
            create_conversation(resources), allowed_user_ids=allowed, bot_user_id=bot_id
        )
        transport = OneBotWebSocketTransport(
            adapter,
            onebot_ws_url=os.environ.get("SI_ONEBOT_WS_URL", "ws://127.0.0.1:3001/"),
            onebot_access_token=os.environ.get("SI_ONEBOT_ACCESS_TOKEN") or None,
        )
        asyncio.run(transport.run())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).error(
            "OneBot startup/run failed; check local configuration"
        )
