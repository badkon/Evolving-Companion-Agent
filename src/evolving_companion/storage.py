"""SQLite archive and explicit memory storage for the current Character."""

import sqlite3
import json
import os
import unicodedata
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from evolving_companion.character_state import CharacterState, TEMPORAL_ANCHORS
from evolving_companion.character_life import (
    CharacterLifeContext,
    LifeContextData,
    ProjectedLifeContext,
)
from evolving_companion.observation import MAX_VISIBLE_NPCS, ObservationSnapshot
from evolving_companion.world import WorldEntity
from evolving_companion.npc import NPCRecord
from evolving_companion.world_actions import ActionReason, ActionResult, MoveToIntent

MEMORY_TYPES = frozenset({"episodic", "semantic", "self", "relationship"})
MEMORY_SOURCES = frozenset({"explicit", "observed", "inferred"})
MEMORY_SALIENCES = frozenset({"low", "medium", "high"})
MEMORY_STATUSES = frozenset({"active", "superseded", "archived"})
EVIDENCE_KINDS = frozenset({"archive_message"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN ('episodic', 'semantic', 'self', 'relationship')
    ),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    source TEXT NOT NULL CHECK (source IN ('explicit', 'observed', 'inferred')),
    salience TEXT NOT NULL CHECK (salience IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'archived')),
    created_at TEXT NOT NULL,
    last_recalled_at TEXT,
    supersedes_memory_id TEXT REFERENCES memories(id)
);

CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind = 'archive_message'),
    evidence_ref TEXT NOT NULL CHECK (length(trim(evidence_ref)) > 0),
    PRIMARY KEY (memory_id, evidence_kind, evidence_ref)
);

CREATE TABLE IF NOT EXISTS memory_supersessions (
    old_memory_id TEXT NOT NULL REFERENCES memories(id),
    new_memory_id TEXT NOT NULL REFERENCES memories(id),
    PRIMARY KEY (old_memory_id, new_memory_id),
    CHECK (old_memory_id <> new_memory_id)
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, model_name)
);

CREATE TABLE IF NOT EXISTS character_state (
    character_id TEXT PRIMARY KEY,
    energy TEXT NOT NULL CHECK (energy IN ('low', 'medium', 'high')),
    attention TEXT NOT NULL CHECK (attention IN ('scattered', 'normal', 'focused')),
    mood_tendency TEXT NOT NULL CHECK (mood_tendency IN ('low', 'neutral', 'positive')),
    social_engagement TEXT NOT NULL CHECK (
        social_engagement IN ('withdrawn', 'normal', 'engaged')
    ),
    current_activity TEXT,
    updated_at TEXT NOT NULL,
    energy_updated_at TEXT NOT NULL,
    attention_updated_at TEXT NOT NULL,
    mood_updated_at TEXT NOT NULL,
    social_updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_runtime (
    character_id TEXT PRIMARY KEY,
    last_interaction_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS world_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type = 'place'),
    canonical_name TEXT NOT NULL CHECK (length(trim(canonical_name)) > 0),
    parent_entity_id TEXT REFERENCES world_entities(entity_id) DEFERRABLE INITIALLY DEFERRED,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (parent_entity_id IS NULL OR parent_entity_id <> entity_id)
);

CREATE TABLE IF NOT EXISTS world_npcs (
    npc_id TEXT PRIMARY KEY NOT NULL,
    canonical_name TEXT NOT NULL CHECK (length(trim(canonical_name)) > 0),
    display_name TEXT,
    place_entity_id TEXT REFERENCES world_entities(entity_id),
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    tags TEXT NOT NULL,
    short_description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS world_npcs_active ON world_npcs(active);
CREATE INDEX IF NOT EXISTS world_npcs_place ON world_npcs(place_entity_id);
CREATE TRIGGER IF NOT EXISTS world_npcs_insert_place
BEFORE INSERT ON world_npcs
WHEN NEW.place_entity_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM world_entities
    WHERE entity_id = NEW.place_entity_id AND entity_type = 'place'
)
BEGIN
    SELECT RAISE(ABORT, 'NPC location must reference an existing Place');
END;
CREATE TRIGGER IF NOT EXISTS world_npcs_update_place
BEFORE UPDATE OF place_entity_id ON world_npcs
WHEN NEW.place_entity_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM world_entities
    WHERE entity_id = NEW.place_entity_id AND entity_type = 'place'
)
BEGIN
    SELECT RAISE(ABORT, 'NPC location must reference an existing Place');
END;

CREATE TABLE IF NOT EXISTS character_life_context (
    character_id TEXT PRIMARY KEY,
    life_stage TEXT NOT NULL CHECK (life_stage IN ('student', 'worker', 'unemployed', 'unknown')),
    home_reference TEXT,
    school_reference TEXT,
    primary_area_reference TEXT,
    current_location_reference TEXT,
    current_role TEXT,
    updated_at TEXT NOT NULL,
    home_entity_id TEXT REFERENCES world_entities(entity_id),
    school_entity_id TEXT REFERENCES world_entities(entity_id),
    primary_area_entity_id TEXT REFERENCES world_entities(entity_id),
    current_location_entity_id TEXT REFERENCES world_entities(entity_id),
    world_references_migrated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS world_action_results (
    action_id TEXT PRIMARY KEY NOT NULL,
    character_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type = 'move_to'),
    destination_entity_id TEXT NOT NULL,
    expected_location_entity_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'rejected')),
    reason TEXT NOT NULL CHECK (reason IN (
        'moved', 'already_at_destination', 'invalid_destination',
        'location_precondition_failed', 'character_id_mismatch',
        'life_context_missing', 'clock_moved_backwards'
    )),
    location_before TEXT,
    location_after TEXT,
    resolved_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_uuid(value: str, name: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{name} must be a UUID string") from error


def _require_choice(value: str, choices: frozenset[str], name: str) -> None:
    if value not in choices:
        raise ValueError(f"Invalid {name}: {value}")


def _require_content(content: str) -> None:
    if not content.strip():
        raise ValueError("content must not be empty")


def _normalize_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    return " ".join(normalized.split()).casefold()


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    memory_type: str
    content: str
    source: str
    salience: str
    status: str
    created_at: str
    last_recalled_at: str | None
    supersedes_memory_id: str | None


@dataclass(frozen=True)
class MemoryEvidence:
    memory_id: str
    evidence_kind: str
    evidence_ref: str


@dataclass(frozen=True)
class MemoryEmbedding:
    memory_id: str
    model_name: str
    dimensions: int
    embedding: bytes
    created_at: str


class SQLiteStore:
    """Persist raw messages and manually supplied memories in one SQLite file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path
            if path is not None
            else os.environ.get("SI_RUNTIME_DB", "runtime/si_001.db")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            # A4/B1 compatibility: retain the existing row and backfill each
            # new field anchor from its last known global State timestamp.
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(character_state)")
                }
                for anchor in TEMPORAL_ANCHORS.values():
                    if anchor not in columns:
                        connection.execute(
                            f"ALTER TABLE character_state ADD COLUMN {anchor} TEXT"
                        )
                    connection.execute(
                        f"UPDATE character_state SET {anchor} = updated_at WHERE {anchor} IS NULL"
                    )
                life_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(character_life_context)"
                    )
                }
                for name in (
                    "home_entity_id",
                    "school_entity_id",
                    "primary_area_entity_id",
                    "current_location_entity_id",
                ):
                    if name not in life_columns:
                        connection.execute(
                            f"ALTER TABLE character_life_context ADD COLUMN {name} "
                            "TEXT REFERENCES world_entities(entity_id)"
                        )
                if "world_references_migrated" not in life_columns:
                    connection.execute(
                        "ALTER TABLE character_life_context ADD COLUMN "
                        "world_references_migrated INTEGER NOT NULL DEFAULT 0"
                    )

    @staticmethod
    def _npc_from_row(row: sqlite3.Row) -> NPCRecord:
        values = dict(row)
        values["tags"] = json.loads(values["tags"])
        return NPCRecord.model_validate(values)

    def get_npc(self, npc_id: UUID) -> NPCRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM world_npcs WHERE npc_id = ?", (str(npc_id),)
            ).fetchone()
        return self._npc_from_row(row) if row is not None else None

    def list_active_npcs(self) -> tuple[NPCRecord, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM world_npcs WHERE active = 1 ORDER BY canonical_name, npc_id"
            ).fetchall()
        return tuple(self._npc_from_row(row) for row in rows)

    @staticmethod
    def _npc_values(npc: NPCRecord) -> tuple[object, ...]:
        return (
            npc.canonical_name,
            npc.display_name,
            str(npc.place_entity_id) if npc.place_entity_id is not None else None,
            int(npc.active),
            json.dumps(npc.tags, ensure_ascii=False),
            npc.short_description,
            npc.created_at.isoformat(),
            npc.updated_at.isoformat(),
            str(npc.npc_id),
        )

    def insert_npc(self, npc: NPCRecord) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO world_npcs
                   (canonical_name, display_name, place_entity_id, active, tags,
                    short_description, created_at, updated_at, npc_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._npc_values(npc),
            )

    def update_npc(self, npc: NPCRecord) -> None:
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                """UPDATE world_npcs SET canonical_name = ?, display_name = ?,
                   place_entity_id = ?, active = ?, tags = ?, short_description = ?,
                   updated_at = ? WHERE npc_id = ?""",
                (
                    *self._npc_values(npc)[:6],
                    npc.updated_at.isoformat(),
                    str(npc.npc_id),
                ),
            )
            if updated.rowcount != 1:
                raise KeyError("NPC does not exist")

    def get_world_entity(self, entity_id: UUID) -> WorldEntity | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM world_entities WHERE entity_id = ?", (str(entity_id),)
            ).fetchone()
        return WorldEntity.model_validate(dict(row)) if row is not None else None

    def list_world_entities(self) -> tuple[WorldEntity, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM world_entities ORDER BY canonical_name, entity_id"
            ).fetchall()
        return tuple(WorldEntity.model_validate(dict(row)) for row in rows)

    def insert_world_seed(self, entities: tuple[WorldEntity, ...]) -> None:
        with closing(self._connect()) as connection, connection:
            for entity in entities:
                connection.execute(
                    """INSERT INTO world_entities VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(entity_id) DO NOTHING""",
                    (
                        str(entity.entity_id),
                        entity.entity_type,
                        entity.canonical_name,
                        str(entity.parent_entity_id)
                        if entity.parent_entity_id
                        else None,
                        entity.description,
                        entity.created_at.isoformat(),
                        entity.updated_at.isoformat(),
                    ),
                )

    def migrate_life_references(
        self, names: dict[str, tuple[UUID, ...]]
    ) -> tuple[str, ...]:
        """Convert legacy names once; retain original columns for inspection."""
        diagnostics: list[str] = []
        fields = ("home", "school", "primary_area", "current_location")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM character_life_context WHERE world_references_migrated = 0"
            ).fetchall()
            for row in rows:
                values: list[str | None] = []
                for field in fields:
                    legacy = row[f"{field}_reference"]
                    matches = names.get(legacy, ()) if legacy is not None else ()
                    values.append(str(matches[0]) if len(matches) == 1 else None)
                    if legacy is not None and len(matches) != 1:
                        diagnostics.append(
                            f"unresolved_legacy_place:{row['character_id']}:{field}"
                        )
                connection.execute(
                    """UPDATE character_life_context SET home_entity_id = ?,
                       school_entity_id = ?, primary_area_entity_id = ?,
                       current_location_entity_id = ?, world_references_migrated = 1
                       WHERE character_id = ?""",
                    (*values, row["character_id"]),
                )
        return tuple(diagnostics)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def get_last_interaction_at(self, character_id: UUID) -> datetime | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT last_interaction_at FROM character_runtime WHERE character_id = ?",
                (str(character_id),),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def get_character_life_context(
        self, character_id: UUID
    ) -> CharacterLifeContext | None:
        with closing(self._connect()) as connection:
            return self._read_life_context(connection, character_id)

    @staticmethod
    def _read_life_context(
        connection: sqlite3.Connection, character_id: UUID
    ) -> CharacterLifeContext | None:
        row = connection.execute(
            """SELECT character_id, life_stage, home_entity_id, school_entity_id,
               primary_area_entity_id, current_location_entity_id, current_role, updated_at,
               world_references_migrated FROM character_life_context WHERE character_id = ?""",
            (str(character_id),),
        ).fetchone()
        if row is None:
            return None
        values = dict(row)
        if not values.pop("world_references_migrated"):
            raise ValueError("Initialize World seed before reading legacy Life Context")
        return CharacterLifeContext.model_validate(values)

    @staticmethod
    def _validate_life_places(
        connection: sqlite3.Connection, context: CharacterLifeContext
    ) -> None:
        for entity_id in (
            context.home_entity_id,
            context.school_entity_id,
            context.primary_area_entity_id,
            context.current_location_entity_id,
        ):
            if (
                entity_id is not None
                and connection.execute(
                    "SELECT 1 FROM world_entities WHERE entity_id = ? AND entity_type = 'place'",
                    (str(entity_id),),
                ).fetchone()
                is None
            ):
                raise ValueError("Life reference must point to an existing Place")

    def initialize_character_life_context(
        self, context: CharacterLifeContext
    ) -> CharacterLifeContext:
        """Insert only if still missing; never overwrite a concurrent initializer."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._read_life_context(connection, context.character_id)
            if existing is not None:
                return existing
            self._validate_life_places(connection, context)
            values = context.model_dump(mode="json")
            values["updated_at"] = context.updated_at.isoformat()
            columns = tuple(values)
            inserted = connection.execute(
                f"INSERT INTO character_life_context ({', '.join(columns)}, world_references_migrated) "
                f"VALUES ({', '.join('?' for _ in columns)}, 1)",
                tuple(values.values()),
            )
            if inserted.rowcount != 1:
                raise sqlite3.IntegrityError(
                    "Life initialization did not insert one row"
                )
            return context

    def patch_character_life_context(
        self, character_id: UUID, changes: LifeContextData, now: datetime
    ) -> CharacterLifeContext:
        """Read latest + validate + patch explicit fields in one write transaction."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Life update time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        # Revalidate the boundary; the SQL field whitelist is LifeContextData only.
        fields = LifeContextData.model_validate(changes.model_dump(exclude_unset=True))
        values = fields.model_dump(exclude_unset=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_life_context(connection, character_id)
            if current is None:
                raise KeyError("Life Context does not exist")
            candidate = CharacterLifeContext.model_validate(
                current.model_dump() | values
            )
            self._validate_life_places(connection, candidate)
            if candidate == current:
                return current
            if now < current.updated_at:
                raise ValueError(
                    "clock_moved_backwards; Life Context update not applied"
                )
            updated = CharacterLifeContext.model_validate(
                candidate.model_dump() | {"updated_at": now}
            )
            encoded = fields.model_dump(mode="json", exclude_unset=True)
            assignments = ", ".join(f"{field} = ?" for field in encoded)
            patched = connection.execute(
                f"UPDATE character_life_context SET {assignments}, updated_at = ? WHERE character_id = ?",
                (*encoded.values(), now.isoformat(), str(character_id)),
            )
            if patched.rowcount != 1:
                raise sqlite3.IntegrityError("Life patch did not update one row")
            return updated

    @staticmethod
    def _read_action_receipt(
        connection: sqlite3.Connection, action_id: UUID
    ) -> tuple[MoveToIntent, ActionResult] | None:
        row = connection.execute(
            "SELECT * FROM world_action_results WHERE action_id = ?", (str(action_id),)
        ).fetchone()
        if row is None:
            return None
        intent = MoveToIntent.model_validate(
            {field: row[field] for field in MoveToIntent.model_fields}
        )
        result = ActionResult.model_validate(
            {
                field: row[field]
                for field in ActionResult.model_fields
                if field in row.keys()
            }
        )
        return intent, result

    def get_world_action_result(self, action_id: UUID) -> ActionResult | None:
        try:
            with closing(self._connect()) as connection:
                receipt = self._read_action_receipt(connection, action_id)
                return receipt[1] if receipt is not None else None
        except (sqlite3.Error, ValueError):
            # None means absent, not lookup failure; suppress private exception text.
            raise RuntimeError("action_result_lookup_failed") from None

    @staticmethod
    def _insert_action_receipt(
        connection: sqlite3.Connection, intent: MoveToIntent, result: ActionResult
    ) -> None:
        values = intent.model_dump(mode="json") | result.model_dump(
            mode="json", exclude={"replayed", "outcome_known"}
        )
        values["resolved_at"] = result.resolved_at.isoformat()
        inserted = connection.execute(
            f"INSERT INTO world_action_results ({', '.join(values)}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
        if inserted.rowcount != 1:
            raise sqlite3.IntegrityError("Action receipt did not insert one row")

    def _resolve_move_to(
        self,
        connection: sqlite3.Connection,
        intent: MoveToIntent,
        character_id: UUID,
        stamp: ActionResult,
    ) -> ActionResult:
        receipt = self._read_action_receipt(connection, intent.action_id)
        if receipt is not None:
            original_intent, original_result = receipt
            if original_intent != intent:
                return ActionResult.model_validate(
                    stamp.model_dump()
                    | {"status": "rejected", "reason": "action_id_conflict"}
                )
            if (
                intent.character_id != character_id
                and original_result.status == "success"
            ):
                return ActionResult.model_validate(
                    stamp.model_dump()
                    | {"status": "rejected", "reason": "character_id_mismatch"}
                )
            return ActionResult.model_validate(
                original_result.model_dump() | {"replayed": True}
            )

        life = (
            None
            if intent.character_id != character_id
            else self._read_life_context(connection, character_id)
        )
        before = life.current_location_entity_id if life is not None else None
        reason: ActionReason
        if intent.character_id != character_id:
            reason = "character_id_mismatch"
        elif life is None:
            reason = "life_context_missing"
        elif (
            connection.execute(
                "SELECT 1 FROM world_entities WHERE entity_id = ? AND entity_type = 'place'",
                (str(intent.destination_entity_id),),
            ).fetchone()
            is None
        ):
            reason = "invalid_destination"
        elif before != intent.expected_location_entity_id:
            reason = "location_precondition_failed"
        elif before == intent.destination_entity_id:
            reason = "already_at_destination"
        elif stamp.resolved_at < life.updated_at:
            reason = "clock_moved_backwards"
        else:
            reason = "moved"
        result = ActionResult.model_validate(
            stamp.model_dump()
            | {
                "status": "success"
                if reason in {"moved", "already_at_destination"}
                else "rejected",
                "reason": reason,
                "location_before": before,
                "location_after": intent.destination_entity_id
                if reason == "moved"
                else before,
            }
        )
        if reason == "moved":
            moved = connection.execute(
                "UPDATE character_life_context SET current_location_entity_id = ?, updated_at = ? WHERE character_id = ?",
                (
                    str(intent.destination_entity_id),
                    result.resolved_at.isoformat(),
                    str(character_id),
                ),
            )
            if moved.rowcount != 1:
                raise sqlite3.IntegrityError("Action did not update one Life row")
        self._insert_action_receipt(connection, intent, result)
        return result

    def resolve_world_action(
        self, intent: MoveToIntent, character_id: UUID, resolved_at: datetime
    ) -> ActionResult:
        """Location + terminal receipt commit atomically; never auto-retry."""
        failure = ActionResult(
            action_id=intent.action_id,
            character_id=intent.character_id,
            destination_entity_id=intent.destination_entity_id,
            resolved_at=resolved_at,
            status="failed",
            reason="storage_error",
        )
        try:
            connection = self._connect()
        except sqlite3.Error:
            return failure  # No transaction could start.
        with closing(connection):
            commit_attempted = False
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = self._resolve_move_to(
                    connection, intent, character_id, failure
                )
                commit_attempted = True
                connection.commit()
                return result  # Only after successful COMMIT, including replay.
            except (sqlite3.Error, ValueError):
                rollback_known = False
                try:
                    # After an ambiguous COMMIT with no active transaction, a
                    # successful rollback() is a no-op, not proof of rollback.
                    if not commit_attempted or connection.in_transaction:
                        connection.rollback()
                        rollback_known = True
                except sqlite3.Error:
                    pass
                return ActionResult.model_validate(
                    failure.model_dump()
                    | {
                        "outcome_known": rollback_known,
                        "reason": "storage_error"
                        if rollback_known
                        else "commit_outcome_unknown",
                    }
                )

    def capture_observation_context(
        self, character_id: UUID, observed_at: datetime
    ) -> tuple[ProjectedLifeContext | None, ObservationSnapshot]:
        """One read snapshot for Life names, current Place and bounded NPC refs."""
        unknown = ObservationSnapshot(
            character_id=character_id, observed_at=observed_at
        )
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            row = connection.execute(
                """SELECT character_id, life_stage, home_entity_id, school_entity_id,
                   primary_area_entity_id, current_location_entity_id, current_role,
                   updated_at, world_references_migrated
                   FROM character_life_context WHERE character_id = ?""",
                (str(character_id),),
            ).fetchone()
            if row is None:
                return None, unknown  # Never bootstrap Life from an observation.
            values = dict(row)
            if not values.pop("world_references_migrated"):
                raise ValueError(
                    "Initialize World seed before reading legacy Life Context"
                )
            life = CharacterLifeContext.model_validate(values)

            def name(entity_id: UUID | None) -> str | None:
                if entity_id is None:
                    return None
                place = connection.execute(
                    "SELECT canonical_name FROM world_entities "
                    "WHERE entity_id = ? AND entity_type = 'place'",
                    (str(entity_id),),
                ).fetchone()
                if place is None:
                    raise ValueError("Life reference must point to an existing Place")
                return place[0]

            projected = ProjectedLifeContext(
                life.life_stage,
                life.current_role,
                name(life.home_entity_id),
                name(life.school_entity_id),
                name(life.primary_area_entity_id),
                name(life.current_location_entity_id),
            )
            if life.current_location_entity_id is None:
                return projected, unknown  # No global NPC query.
            rows = connection.execute(
                "SELECT npc_id FROM world_npcs "
                "WHERE place_entity_id = ? AND active = 1 "
                "ORDER BY npc_id LIMIT ?",
                (str(life.current_location_entity_id), MAX_VISIBLE_NPCS + 1),
            ).fetchall()
            snapshot = ObservationSnapshot(
                character_id=character_id,
                observed_at=observed_at,
                location_entity_id=life.current_location_entity_id,
                place_name=projected.current_location_reference,
                visible_npc_ids=tuple(
                    UUID(item[0]) for item in rows[:MAX_VISIBLE_NPCS]
                ),
                truncated=len(rows) > MAX_VISIBLE_NPCS,
                status="available",
            )
            return projected, snapshot

    def upsert_character_life_context(self, context: CharacterLifeContext) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO character_life_context
                   (character_id, life_stage, home_entity_id, school_entity_id,
                    primary_area_entity_id, current_location_entity_id, current_role, updated_at,
                    world_references_migrated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(character_id) DO UPDATE SET
                       life_stage = excluded.life_stage,
                       home_entity_id = excluded.home_entity_id,
                       school_entity_id = excluded.school_entity_id,
                       primary_area_entity_id = excluded.primary_area_entity_id,
                       current_location_entity_id = excluded.current_location_entity_id,
                       current_role = excluded.current_role,
                       updated_at = excluded.updated_at""",
                (
                    str(context.character_id),
                    context.life_stage,
                    str(context.home_entity_id) if context.home_entity_id else None,
                    str(context.school_entity_id) if context.school_entity_id else None,
                    str(context.primary_area_entity_id)
                    if context.primary_area_entity_id
                    else None,
                    str(context.current_location_entity_id)
                    if context.current_location_entity_id
                    else None,
                    context.current_role,
                    context.updated_at.astimezone(timezone.utc).isoformat(),
                ),
            )

    def record_last_interaction(self, character_id: UUID, at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("interaction time must be timezone-aware")
        at = at.astimezone(timezone.utc)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT last_interaction_at FROM character_runtime WHERE character_id = ?",
                (str(character_id),),
            ).fetchone()
            existing = datetime.fromisoformat(row[0]) if row and row[0] else None
            persisted = existing if existing is not None and existing > at else at
            connection.execute(
                """INSERT INTO character_runtime(character_id, last_interaction_at, updated_at)
                   VALUES (?, ?, ?) ON CONFLICT(character_id) DO UPDATE SET
                   last_interaction_at=excluded.last_interaction_at,
                   updated_at=excluded.updated_at""",
                (str(character_id), persisted.isoformat(), _utc_now()),
            )
        return persisted

    def get_character_state(self, character_id: UUID) -> CharacterState | None:
        """Load one state using the Character's stable internal UUID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM character_state WHERE character_id = ?",
                (str(character_id),),
            ).fetchone()
        if row is None:
            return None
        values = dict(row)
        values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        return CharacterState.model_validate(values)

    def upsert_character_state(self, state: CharacterState) -> None:
        """Insert or replace the current state for its internal UUID."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO character_state
                   (character_id, energy, attention, mood_tendency,
                    social_engagement, current_activity, updated_at,
                    energy_updated_at, attention_updated_at, mood_updated_at, social_updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(character_id) DO UPDATE SET
                       energy = excluded.energy,
                       attention = excluded.attention,
                       mood_tendency = excluded.mood_tendency,
                       social_engagement = excluded.social_engagement,
                       current_activity = excluded.current_activity,
                       updated_at = excluded.updated_at,
                       energy_updated_at = excluded.energy_updated_at,
                       attention_updated_at = excluded.attention_updated_at,
                       mood_updated_at = excluded.mood_updated_at,
                       social_updated_at = excluded.social_updated_at""",
                (
                    str(state.character_id),
                    state.energy,
                    state.attention,
                    state.mood_tendency,
                    state.social_engagement,
                    state.current_activity,
                    state.updated_at.astimezone(timezone.utc).isoformat(),
                    state.energy_updated_at.isoformat(),
                    state.attention_updated_at.isoformat(),
                    state.mood_updated_at.isoformat(),
                    state.social_updated_at.isoformat(),
                ),
            )

    def append_archive_message(
        self, conversation_id: str, role: str, content: str
    ) -> str:
        """Commit one original message before returning its stable ID."""
        _require_uuid(conversation_id, "conversation_id")
        _require_choice(role, frozenset({"user", "assistant"}), "role")
        _require_content(content)
        message_id = str(uuid4())
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO archive_messages
                   (id, conversation_id, role, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (message_id, conversation_id, role, content, _utc_now()),
            )
        return message_id

    def create_memory(
        self,
        memory_type: str,
        content: str,
        source: str,
        salience: str,
        status: str = "active",
        supersedes_memory_id: str | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> MemoryRecord:
        """Save an explicitly gated memory with optional archive evidence."""
        _require_choice(memory_type, MEMORY_TYPES, "memory_type")
        _require_content(content)
        _require_choice(source, MEMORY_SOURCES, "source")
        _require_choice(salience, MEMORY_SALIENCES, "salience")
        _require_choice(status, MEMORY_STATUSES, "status")
        if supersedes_memory_id is not None:
            _require_uuid(supersedes_memory_id, "supersedes_memory_id")
        for evidence_ref in evidence_refs:
            _require_uuid(evidence_ref, "evidence_ref")
        record = MemoryRecord(
            id=str(uuid4()),
            memory_type=memory_type,
            content=content,
            source=source,
            salience=salience,
            status=status,
            created_at=_utc_now(),
            last_recalled_at=None,
            supersedes_memory_id=supersedes_memory_id,
        )
        with closing(self._connect()) as connection, connection:
            for evidence_ref in evidence_refs:
                archive_row = connection.execute(
                    "SELECT 1 FROM archive_messages WHERE id = ?", (evidence_ref,)
                ).fetchone()
                if archive_row is None:
                    raise ValueError(
                        "archive evidence must reference an existing message"
                    )
            connection.execute(
                """INSERT INTO memories
                   (id, memory_type, content, source, salience, status,
                    created_at, last_recalled_at, supersedes_memory_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.memory_type,
                    record.content,
                    record.source,
                    record.salience,
                    record.status,
                    record.created_at,
                    record.last_recalled_at,
                    record.supersedes_memory_id,
                ),
            )
            connection.executemany(
                """INSERT INTO memory_evidence
                   (memory_id, evidence_kind, evidence_ref) VALUES (?, ?, ?)""",
                [
                    (record.id, "archive_message", evidence_ref)
                    for evidence_ref in evidence_refs
                ],
            )
        return record

    def has_active_memory_content(self, content: str) -> bool:
        """Check exact or whitespace/Unicode-normalized active content."""
        target = _normalize_content(content)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT content FROM memories WHERE status = 'active'"
            ).fetchall()
        return any(_normalize_content(row["content"]) == target for row in rows)

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return MemoryRecord(**dict(row)) if row is not None else None

    def list_active_memories(self) -> tuple[MemoryRecord, ...]:
        """Return active memories in a stable order for indexing and retrieval."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE status = 'active' ORDER BY created_at, id"
            ).fetchall()
        return tuple(MemoryRecord(**dict(row)) for row in rows)

    def supersede_memories(
        self, old_memory_ids: Sequence[str], new_memory_id: str
    ) -> tuple[MemoryRecord, ...]:
        """Atomically mark active old memories superseded by one active new memory."""
        _require_uuid(new_memory_id, "new_memory_id")
        unique_old_ids = tuple(dict.fromkeys(old_memory_ids))
        for memory_id in unique_old_ids:
            _require_uuid(memory_id, "old_memory_id")
        if new_memory_id in unique_old_ids:
            raise ValueError("a memory cannot supersede itself")
        if not unique_old_ids:
            return ()

        placeholders = ", ".join("?" for _ in unique_old_ids)
        with closing(self._connect()) as connection, connection:
            new_row = connection.execute(
                "SELECT status FROM memories WHERE id = ?", (new_memory_id,)
            ).fetchone()
            if new_row is None or new_row["status"] != "active":
                raise ValueError("new memory must exist and be active")

            old_rows = connection.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})",
                unique_old_ids,
            ).fetchall()
            if len(old_rows) != len(unique_old_ids) or any(
                row["status"] != "active" for row in old_rows
            ):
                raise ValueError("all old memories must exist and be active")

            updated = connection.executemany(
                "UPDATE memories SET status = 'superseded' "
                "WHERE id = ? AND status = 'active'",
                [(memory_id,) for memory_id in unique_old_ids],
            )
            if updated.rowcount != len(unique_old_ids):
                raise ValueError("old memory status changed during supersede")
            connection.executemany(
                """INSERT INTO memory_supersessions
                   (old_memory_id, new_memory_id) VALUES (?, ?)""",
                [(memory_id, new_memory_id) for memory_id in unique_old_ids],
            )
        return tuple(
            MemoryRecord(**{**dict(row), "status": "superseded"}) for row in old_rows
        )

    def get_superseded_memories(self, new_memory_id: str) -> tuple[MemoryRecord, ...]:
        """Return historical memories linked to the given superseding memory."""
        _require_uuid(new_memory_id, "new_memory_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT memories.* FROM memories
                   JOIN memory_supersessions
                     ON memory_supersessions.old_memory_id = memories.id
                   WHERE memory_supersessions.new_memory_id = ?
                   ORDER BY memories.created_at, memories.id""",
                (new_memory_id,),
            ).fetchall()
        return tuple(MemoryRecord(**dict(row)) for row in rows)

    def get_memory_embeddings(
        self, memory_ids: Sequence[str], model_name: str
    ) -> dict[str, MemoryEmbedding]:
        """Read cached vectors for one model, keyed by memory ID."""
        if not memory_ids:
            return {}
        placeholders = ", ".join("?" for _ in memory_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT memory_id, model_name, dimensions, embedding, created_at
                    FROM memory_embeddings
                    WHERE model_name = ? AND memory_id IN ({placeholders})""",
                (model_name, *memory_ids),
            ).fetchall()
        return {row["memory_id"]: MemoryEmbedding(**dict(row)) for row in rows}

    def upsert_memory_embeddings(
        self,
        embeddings: Sequence[MemoryEmbedding],
    ) -> None:
        """Write derived vectors without changing authoritative memory records."""
        if not embeddings:
            return
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """INSERT INTO memory_embeddings
                   (memory_id, model_name, dimensions, embedding, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(memory_id, model_name) DO UPDATE SET
                       dimensions = excluded.dimensions,
                       embedding = excluded.embedding,
                       created_at = excluded.created_at""",
                [
                    (
                        item.memory_id,
                        item.model_name,
                        item.dimensions,
                        item.embedding,
                        item.created_at,
                    )
                    for item in embeddings
                ],
            )

    def add_evidence(
        self, memory_id: str, evidence_kind: str, evidence_ref: str
    ) -> MemoryEvidence:
        _require_uuid(memory_id, "memory_id")
        _require_choice(evidence_kind, EVIDENCE_KINDS, "evidence_kind")
        _require_uuid(evidence_ref, "evidence_ref")
        with closing(self._connect()) as connection, connection:
            archive_row = connection.execute(
                "SELECT 1 FROM archive_messages WHERE id = ?", (evidence_ref,)
            ).fetchone()
            if archive_row is None:
                raise ValueError("archive evidence must reference an existing message")
            connection.execute(
                """INSERT INTO memory_evidence
                   (memory_id, evidence_kind, evidence_ref) VALUES (?, ?, ?)""",
                (memory_id, evidence_kind, evidence_ref),
            )
        return MemoryEvidence(memory_id, evidence_kind, evidence_ref)

    def get_evidence(self, memory_id: str) -> tuple[MemoryEvidence, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT memory_id, evidence_kind, evidence_ref
                   FROM memory_evidence WHERE memory_id = ?
                   ORDER BY rowid""",
                (memory_id,),
            ).fetchall()
        return tuple(MemoryEvidence(**dict(row)) for row in rows)
