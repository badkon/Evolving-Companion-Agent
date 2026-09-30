from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import (
    CharacterSeedData,
    load_character_seed_data,
)
from evolving_companion.character_life import (
    CharacterLifeContext,
    CharacterLifeService,
    LifeContextData,
)
from evolving_companion.character_projection import (
    CharacterProjector,
    ProjectedCharacterContext,
)
from evolving_companion.character_state import CharacterStateService
from evolving_companion.cli import _handle_life_command
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore

T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def seed() -> CharacterSeedData:
    return load_character_seed_data(
        Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    )


def make_service(
    path: Path, seed: CharacterSeedData
) -> tuple[SQLiteStore, FixedClock, CharacterLifeService]:
    store = SQLiteStore(path)
    clock = FixedClock(T0)
    return (
        store,
        clock,
        CharacterLifeService(
            store, seed.identity.internal_id, seed.initial_life_context, clock
        ),
    )


def test_seed_defaults_initialize_once_with_internal_uuid(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    store, _, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id
    context = service.get_life_context(key)
    assert context.character_id == key
    assert context.life_stage == "student" and context.current_role == "学生"
    assert context.home_reference == "住宅区的家"
    assert context.school_reference == "学校"
    assert context.primary_area_reference == "教育生活区"
    assert context.current_location_reference is None
    assert context.updated_at == T0
    with closing(sqlite3.connect(store.path)) as connection:
        row = connection.execute(
            "SELECT character_id FROM character_life_context"
        ).fetchone()
        assert row == (str(key),)
    with pytest.raises(ValueError, match="internal_id"):
        service.get_life_context(UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
    with pytest.raises(ValidationError):
        CharacterLifeContext.model_validate(
            {"character_id": "SI-001", "updated_at": T0}
        )


def test_partial_update_clear_and_restart_preserve_runtime(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    path = tmp_path / "life.db"
    store, clock, service = make_service(path, seed)
    key = seed.identity.internal_id
    initial = service.get_life_context(key)
    clock.advance(hours=1)
    changed = service.update_life_context(key, current_location_reference="学校")
    assert changed.current_location_reference == "学校"
    assert changed.updated_at == clock.now_utc()
    assert changed.model_dump(
        exclude={"current_location_reference", "updated_at"}
    ) == initial.model_dump(exclude={"current_location_reference", "updated_at"})
    restarted = CharacterLifeService(SQLiteStore(path), key, LifeContextData(), clock)
    assert restarted.get_life_context(key) == changed
    cleared = service.update_life_context(
        key, current_location_reference=None, current_role=None
    )
    assert cleared.current_location_reference is None and cleared.current_role is None
    assert cleared.home_reference == initial.home_reference
    assert (
        CharacterLifeService(
            SQLiteStore(store.path), key, seed.initial_life_context, clock
        ).get_life_context(key)
        == cleared
    )


def test_read_noop_and_time_passage_do_not_refresh_or_move_context(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    _, clock, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id
    before = service.update_life_context(key, current_location_reference="学校")
    clock.advance(hours=12)
    assert service.get_life_context(key) == before
    assert service.update_life_context(key) == before
    assert service.update_life_context(key, current_location_reference="学校") == before
    assert service.get_life_context(key).updated_at == T0


def test_all_nullable_fields_can_be_explicitly_cleared(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    _, _, service = make_service(tmp_path / "life.db", seed)
    context = service.update_life_context(
        seed.identity.internal_id,
        home_reference=None,
        school_reference=None,
        primary_area_reference=None,
        current_location_reference=None,
        current_role=None,
    )
    assert context.home_reference is None and context.school_reference is None
    assert (
        context.primary_area_reference is None
        and context.current_location_reference is None
    )
    assert context.current_role is None and context.life_stage == "student"


def test_life_updates_do_not_touch_state_or_memory(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    store, clock, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id
    state = CharacterStateService(store, clock).update_state(
        key, current_activity="看漫画"
    )
    memory = store.create_memory("semantic", "测试记忆", "explicit", "low")
    service.update_life_context(
        key, current_location_reference="学校", current_role="学生"
    )
    assert store.get_character_state(key) == state
    assert store.list_active_memories() == (memory,)
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM archive_messages").fetchone()[0]
            == 0
        )


def test_prompt_projects_known_life_fields_and_preserves_existing_rules(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    _, _, service = make_service(tmp_path / "life.db", seed)
    context = service.get_life_context(seed.identity.internal_id)
    builder = PromptBuilder(CharacterProjector().project(seed))
    baseline = builder.build([], "你好")[0]["content"]
    system = builder.build([], "你好", character_life_context=context)[0]["content"]
    assert system.startswith(baseline)
    block = system.split("【当前生活上下文】", 1)[1]
    assert "生活阶段：学生" in block and "当前身份：学生" in block
    assert "家：住宅区的家" in block and "学校：学校" in block
    assert "主要生活区域：教育生活区" in block
    assert "当前地点：" not in block and "未知" not in block
    for name in CharacterLifeContext.model_fields:
        assert name not in block
    assert str(context.character_id) not in block and T0.isoformat() not in block
    assert "不自动成为亲历记忆" in block
    assert "外部世界当前状态" in block and "不要据此编造课程" in block
    assert "时间也不能决定位置" in block


def test_unconfigured_context_does_not_emit_unknown_prompt_fields(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    service = CharacterLifeService(
        SQLiteStore(tmp_path / "life.db"),
        seed.identity.internal_id,
        clock=FixedClock(T0),
    )
    context = service.get_life_context(seed.identity.internal_id)
    assert context.life_stage == "unknown"
    system = PromptBuilder(ProjectedCharacterContext("玲")).build(
        [], "你好", character_life_context=context
    )[0]["content"]
    assert "【当前生活上下文】" not in system


def test_conversation_reads_latest_life_context(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    store, clock, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id

    class Client:
        def complete(self, messages: list[Message]) -> str:
            assert "当前地点：学校" in messages[0]["content"]
            return "在呢。"

    conversation = Conversation(
        Client(),
        CharacterProjector().project(seed),
        store,
        character_id=key,
        character_life_service=service,
        clock=clock,
    )
    service.update_life_context(key, current_location_reference="学校")
    before = service.get_life_context(key)
    assert conversation.send("你在哪？") == "在呢。"
    assert service.get_life_context(key) == before


def test_invalid_update_is_atomic_and_rollback_preserves_context(
    tmp_path: Path, seed: CharacterSeedData
) -> None:
    _, clock, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id
    before = service.get_life_context(key)
    with pytest.raises(ValidationError):
        service.update_life_context(
            key, current_location_reference="学校", current_role="   "
        )
    assert service.get_life_context(key) == before
    clock.set(T0 - timedelta(hours=1))
    with pytest.raises(ValueError, match="clock_moved_backwards"):
        service.update_life_context(key, current_location_reference="学校")
    assert service.get_life_context(key) == before


def test_cli_life_commands_update_clear_and_reject_unknown_commands(
    tmp_path: Path, seed: CharacterSeedData, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, service = make_service(tmp_path / "life.db", seed)
    key = seed.identity.internal_id
    _handle_life_command("/life", service, key)
    _handle_life_command("/life location 学校", service, key)
    assert service.get_life_context(key).current_location_reference == "学校"
    _handle_life_command("/life role 学生", service, key)
    assert service.get_life_context(key).current_role == "学生"
    _handle_life_command("/life location none", service, key)
    assert service.get_life_context(key).current_location_reference is None
    _handle_life_command("/life invalid value", service, key)
    assert "生活命令无效" in capsys.readouterr().out


def test_life_context_rejects_invalid_stage_and_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        LifeContextData.model_validate({"life_stage": "astronaut"})
    with pytest.raises(ValidationError):
        CharacterLifeContext.model_validate(
            {"character_id": UUID(int=1), "updated_at": datetime(2026, 9, 30)}
        )
