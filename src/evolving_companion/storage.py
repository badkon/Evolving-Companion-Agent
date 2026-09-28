"""SQLite archive and explicit memory storage for the current Character."""

import sqlite3
import unicodedata
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

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

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, model_name)
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

    def __init__(self, path: str | Path = Path("runtime/si_001.db")) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

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
