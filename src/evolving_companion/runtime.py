"""Shared Core initialization and transport-free server lifetime."""

import asyncio
from contextlib import ExitStack
import logging
import os
import signal
from pathlib import Path

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import SystemClock
from evolving_companion.conversation import Conversation
from evolving_companion.llm import LLMClient
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
from evolving_companion.storage import SQLiteStore
from evolving_companion.observation import ObservationService
from evolving_companion.world import WorldEntityService, load_world_seed
from evolving_companion.world_time import WorldTimeService


def create_conversation(resources: ExitStack) -> Conversation:
    root = Path(__file__).resolve().parents[2]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    store = SQLiteStore(
        os.environ.get("SI_RUNTIME_DB", str(root / "runtime/si_001.db"))
    )
    clock = SystemClock()
    for diagnostic in WorldEntityService(store, clock).initialize_seed_entities(
        load_world_seed(root / "data/worlds/si_world.yaml")
    ):
        logging.getLogger(__name__).warning("World migration: %s", diagnostic)
    life_service = CharacterLifeService(
        store, seed.identity.internal_id, seed.initial_life_context, clock
    )
    life_service.get_life_context(seed.identity.internal_id)
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
        character_life_service=life_service,
        observation_service=ObservationService(
            store, clock, world_time_service=WorldTimeService(seed.timezone, clock)
        ),
    )


async def wait_for_shutdown() -> None:
    """Suspend without polling; signal callbacks wake the same event loop."""
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    previous = {}

    def stop(signum, frame) -> None:
        loop.call_soon_threadsafe(stopped.set)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        logging.getLogger(__name__).info(
            "Core initialized; no chat transport. Waiting for shutdown."
        )
        await stopped.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_core_only() -> int:
    """Own Core resources for the entire idle server lifetime."""
    logging.basicConfig(level=logging.INFO)
    try:
        with ExitStack() as resources:
            conversation = create_conversation(resources)
            asyncio.run(wait_for_shutdown())
            # Keep the initialized Core alive until shutdown, without chat activity.
            del conversation
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        logging.getLogger(__name__).error(
            "Core startup/run failed (%s)", type(error).__name__
        )
        return 1
    return 0
