"""Minimal multi-turn terminal chat for SI-001."""

from pathlib import Path
from typing import cast
from uuid import UUID

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_state import (
    Attention,
    CharacterStateService,
    Energy,
    MoodTendency,
    SocialEngagement,
)
from evolving_companion.character_state_transition import (
    CharacterStateEvent,
    CharacterStateTransitionService,
    StateEventType,
)
from evolving_companion.character_events import (
    ActivityEventPayload,
    CharacterEventService,
    CharacterEventType,
    CharacterEventSource,
)
from evolving_companion.character_state import CharacterState
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
from evolving_companion.storage import SQLiteStore
from evolving_companion.clock import SystemClock
from evolving_companion.time_model import CharacterTimeService, format_offline_duration
from evolving_companion.world import WorldEntityService, load_world_seed


def main() -> None:
    """Read messages, print replies, and stop on ``/exit``."""
    if not load_local_env():
        print(
            "DEEPSEEK_API_KEY is not configured. Set it in the environment or "
            "create .env.local from .env.example."
        )
        return
    try:
        seed_path = (
            Path(__file__).resolve().parents[2] / "data" / "characters" / "si_001.yaml"
        )
        seed_data = load_character_seed_data(seed_path)
        character_context = CharacterProjector().project(seed_data)
        store = SQLiteStore()
        clock = SystemClock()
        world = WorldEntityService(store, clock)
        diagnostics = world.initialize_seed_entities(
            load_world_seed(seed_path.parents[1] / "worlds/si_world.yaml")
        )
        for diagnostic in diagnostics:
            print(f"World migration: {diagnostic}")
        state_service = CharacterStateService(store, clock)
        life_service = CharacterLifeService(
            store, seed_data.identity.internal_id, seed_data.initial_life_context, clock
        )
        transition_service = CharacterStateTransitionService(state_service, clock)
        event_service = CharacterEventService(
            seed_data.identity.internal_id, state_service, transition_service, clock
        )
        time_service = CharacterTimeService(
            store, seed_data.identity.internal_id, seed_data.timezone, clock
        )
        retriever = MemoryRetriever(store)
        recall_service = MemoryRecallService(retriever, MemoryReranker())
        llm_client = LLMClient()
        consolidation_service = MemoryConsolidationService(
            store, retriever, MemoryConsolidationJudge(llm_client)
        )
        formation_service = MemoryFormationService(
            MemoryExtractor(llm_client), store, consolidation_service
        )
        conversation = Conversation(
            llm_client,
            character_context,
            store,
            memory_recall_service=recall_service,
            memory_formation_service=formation_service,
            character_state_service=state_service,
            character_id=seed_data.identity.internal_id,
            character_timezone=seed_data.timezone,
            clock=clock,
            character_life_service=life_service,
        )
    except ValueError as error:
        print(error)
        return
    except OSError as error:
        print(f"无法读取 Character Seed Data：{error}")
        return

    print("和玲聊天。输入 /exit 退出。")
    print("开发状态命令：/state [energy|attention|mood|social|activity|event …]")
    print("开发时间命令：/time")
    print("开发生活命令：/life [location|role <值>]；none 清空字段")
    print("开发地点命令：/world；/life location-id <uuid>")
    while True:
        try:
            user_message = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_message == "/exit":
            break
        if not user_message:
            continue
        if user_message == "/state" or user_message.startswith("/state "):
            _handle_state_command(
                user_message,
                state_service,
                transition_service,
                seed_data.identity.internal_id,
            )
            continue
        if user_message == "/event" or user_message.startswith("/event "):
            _handle_event_command(
                user_message, event_service, seed_data.identity.internal_id
            )
            continue
        if user_message == "/time":
            snapshot = time_service.snapshot()
            offline = (
                "无记录"
                if snapshot.offline_duration is None
                else "暂不可判断"
                if snapshot.diagnostics
                else format_offline_duration(snapshot.offline_duration)
            )
            print(f"UTC: {snapshot.now_utc.isoformat()}")
            print(f"Local: {snapshot.now_local.isoformat()} ({snapshot.timezone})")
            print(f"Last interaction: {snapshot.last_interaction_at or '无记录'}")
            print(f"Offline duration: {offline}")
            continue
        if user_message == "/world":
            for entity in world.list_entities():
                print(f"{entity.canonical_name} | {entity.entity_type}")
            continue
        if user_message == "/life" or user_message.startswith("/life "):
            _handle_life_command(
                user_message, life_service, seed_data.identity.internal_id, world
            )
            continue

        try:
            print(f"玲：{conversation.send(user_message)}")
        except Exception:  # Do not expose provider details or credentials in the CLI.
            print("请求失败，请检查网络与 DeepSeek API 配置后重试。")


def _handle_life_command(
    command: str,
    life_service: CharacterLifeService,
    character_id: UUID,
    world: WorldEntityService,
) -> None:
    """Small local developer interface for explicit Life Context updates."""
    parts = command.split(maxsplit=2)
    try:
        if len(parts) == 1:
            context = life_service.get_life_context(character_id)
        elif len(parts) == 3 and parts[1] == "location":
            context = life_service.update_life_context(
                character_id,
                current_location_entity_id=None
                if parts[2] == "none"
                else world.resolve_place_name(parts[2]),
            )
        elif len(parts) == 3 and parts[1] == "location-id":
            context = life_service.update_life_context(
                character_id, current_location_entity_id=UUID(parts[2])
            )
        elif len(parts) == 3 and parts[1] == "role":
            context = life_service.update_life_context(
                character_id, current_role=None if parts[2] == "none" else parts[2]
            )
        else:
            raise ValueError("用法：/life 或 /life <location|role> <值>；none 表示清空")
    except ValueError as error:
        print(f"生活命令无效：{error}")
        return
    display = life_service.project_context(character_id, world)
    print(
        f"生活上下文：life_stage={context.life_stage}, role={context.current_role or '无'}, "
        f"home={display.home_reference or '无'}, school={display.school_reference or '无'}, "
        f"area={display.primary_area_reference or '无'}, location={display.current_location_reference or '无'}"
    )


def _handle_state_command(
    command: str,
    state_service: CharacterStateService,
    transition_service: CharacterStateTransitionService,
    character_id: UUID,
) -> None:
    """Small local-only developer command for explicit state inspection/updates."""
    parts = command.split(maxsplit=2)
    try:
        if len(parts) == 1:
            state = state_service.get_state(character_id)
            print(
                "当前状态："
                f"energy={state.energy}, attention={state.attention}, "
                f"mood={state.mood_tendency}, social={state.social_engagement}, "
                f"activity={state.current_activity or '无'}"
            )
            return
        if len(parts) < 3:
            raise ValueError(
                "用法：/state <energy|attention|mood|social|activity|event> <值>"
            )
        field, value = parts[1], parts[2]
        if field == "event":
            event_types: dict[str, StateEventType] = {
                "rest": "rest_started",
                "focus-start": "focused_task_started",
                "focus-end": "focused_task_ended",
                "mood-up": "mood_up",
                "mood-down": "mood_down",
                "mood-reset": "mood_reset",
                "social-up": "social_engagement_up",
                "social-down": "social_engagement_down",
            }
            event_type = event_types.get(value)
            if event_type is None:
                raise ValueError(
                    "事件可选 rest、focus-start、focus-end、mood-up、mood-down、"
                    "mood-reset、social-up、social-down。"
                )
            result = transition_service.apply_event(
                character_id, CharacterStateEvent(event_type)
            )
            print(f"{result.reason} changed_fields={result.changed_fields}")
            return
        if field == "energy":
            if value not in {"low", "medium", "high"}:
                raise ValueError("energy 可选 low、medium、high。")
            state = state_service.update_state(character_id, energy=cast(Energy, value))
        elif field == "attention":
            if value not in {"scattered", "normal", "focused"}:
                raise ValueError("attention 可选 scattered、normal、focused。")
            state = state_service.update_state(
                character_id, attention=cast(Attention, value)
            )
        elif field == "mood":
            if value not in {"low", "neutral", "positive"}:
                raise ValueError("mood 可选 low、neutral、positive。")
            state = state_service.update_state(
                character_id, mood_tendency=cast(MoodTendency, value)
            )
        elif field == "social":
            if value not in {"withdrawn", "normal", "engaged"}:
                raise ValueError("social 可选 withdrawn、normal、engaged。")
            state = state_service.update_state(
                character_id, social_engagement=cast(SocialEngagement, value)
            )
        elif field == "activity":
            event_type: StateEventType = (
                "activity_cleared" if value == "none" else "activity_set"
            )
            result = transition_service.apply_event(
                character_id,
                CharacterStateEvent(
                    event_type,
                    activity_name=None if value == "none" else value,
                ),
            )
            print(f"{result.reason} changed_fields={result.changed_fields}")
            return
        else:
            raise ValueError(
                "未知状态字段；可用 energy、attention、mood、social、activity。"
            )
    except (ValueError, TypeError) as error:
        print(f"状态命令无效：{error}")
        return
    print(
        "状态已更新："
        f"energy={state.energy}, attention={state.attention}, "
        f"mood={state.mood_tendency}, social={state.social_engagement}, "
        f"activity={state.current_activity or '无'}"
    )


def _handle_event_command(
    command: str, event_service: CharacterEventService, character_id: UUID
) -> None:
    """Handle a few explicit, local developer event commands."""
    parts = command.split(maxsplit=2)
    if len(parts) < 2:
        print(
            "用法：/event <activity-start|activity-end|rest-start|rest-end|focus-start|focus-end> [活动]"
        )
        return

    command_name = parts[1]
    activity_name = parts[2] if len(parts) == 3 else None
    event_map: dict[str, tuple[CharacterEventType, CharacterEventSource, bool]] = {
        "activity-start": ("activity_started", "activity", True),
        "activity-end": ("activity_ended", "activity", True),
        "rest-start": ("rest_started", "developer", False),
        "rest-end": ("rest_ended", "developer", False),
        "focus-start": ("focused_task_started", "activity", True),
        "focus-end": ("focused_task_ended", "activity", False),
    }
    mapping = event_map.get(command_name)
    if mapping is None:
        print(
            "未知事件；可用 activity-start、activity-end、rest-start、rest-end、focus-start、focus-end。"
        )
        return

    event_type, source, requires_activity = mapping
    if requires_activity and not activity_name:
        print(f"/event {command_name} 需要活动名称。")
        return
    payload = (
        ActivityEventPayload(activity_name=activity_name)
        if activity_name is not None
        and event_type not in {"rest_started", "rest_ended"}
        else None
    )
    try:
        result = event_service.handle(
            event_service.create_event(
                character_id=character_id,
                event_type=event_type,
                source=source,
                payload=payload,
            )
        )
    except ValueError as error:
        print(f"事件无效：{error}")
        return

    print(f"event handled: {result.handled}; {result.reason}")
    print(f"state before: {_format_state(result.before_state)}")
    print(f"state after:  {_format_state(result.after_state)}")


def _format_state(state: CharacterState | None) -> str:
    if state is None:
        return "unavailable"
    return (
        f"energy={state.energy}, attention={state.attention}, "
        f"mood={state.mood_tendency}, social={state.social_engagement}, "
        f"activity={state.current_activity or 'none'}"
    )


if __name__ == "__main__":
    main()
