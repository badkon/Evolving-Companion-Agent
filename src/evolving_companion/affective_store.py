"""Small transactional affective store beside, never inside, Memory tables."""

from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid5

from evolving_companion.affective import (
    AffectiveAppraisal,
    AffectiveSnapshot,
    EmotionEvent,
    Event,
    InteractionEvent,
    Mood,
    RelationshipState,
    apply_emotions,
    relationship_changes,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS affective_state (
 character_id TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS relationship_states (
 character_id TEXT NOT NULL REFERENCES affective_state(character_id),
 target_id TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
 PRIMARY KEY(character_id, target_id)
);
CREATE TABLE IF NOT EXISTS emotion_events (
 character_id TEXT NOT NULL REFERENCES affective_state(character_id),
 source_event_id TEXT NOT NULL, emotion_type TEXT NOT NULL,
 decay_until TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
 PRIMARY KEY(character_id, source_event_id, emotion_type)
);
CREATE TABLE IF NOT EXISTS interaction_events (
 character_id TEXT NOT NULL REFERENCES affective_state(character_id),
 source_event_id TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
 PRIMARY KEY(character_id, source_event_id)
);
CREATE TABLE IF NOT EXISTS affective_receipts (
 character_id TEXT NOT NULL REFERENCES affective_state(character_id),
 source_event_id TEXT NOT NULL, applied_at TEXT NOT NULL,
 PRIMARY KEY(character_id, source_event_id)
);
CREATE TABLE IF NOT EXISTS relationship_budgets (
 character_id TEXT NOT NULL, target_id TEXT NOT NULL,
 day TEXT NOT NULL, spent TEXT NOT NULL CHECK(json_valid(spent)),
 PRIMARY KEY(character_id, target_id),
 FOREIGN KEY(character_id,target_id) REFERENCES relationship_states(character_id,target_id)
);
CREATE TABLE IF NOT EXISTS affective_primary_target (
 character_id TEXT PRIMARY KEY REFERENCES affective_state(character_id), target_id TEXT NOT NULL
);
"""


class AffectiveStore:
    def __init__(
        self, path: Path, character_id: UUID, primary_target: UUID, now: datetime
    ) -> None:
        if not isinstance(character_id, UUID) or not isinstance(primary_target, UUID):
            raise TypeError("Character and relation target keys must be UUIDs")
        self.path, self.character_id = path, str(character_id)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR IGNORE INTO affective_state VALUES (?,?)",
                    (self.character_id, Mood(updated_at=now).model_dump_json()),
                )
                db.execute(
                    "INSERT OR IGNORE INTO affective_primary_target VALUES (?,?)",
                    (self.character_id, str(primary_target)),
                )
                self.primary_target = UUID(
                    db.execute(
                        "SELECT target_id FROM affective_primary_target WHERE character_id=?",
                        (self.character_id,),
                    ).fetchone()[0]
                )
                if (
                    self.primary_target == uuid5(character_id, "local-primary")
                    and primary_target != self.primary_target
                ):
                    # Bind a previously transport-free installation once QQ is configured.
                    self.primary_target = primary_target
                    db.execute(
                        "UPDATE affective_primary_target SET target_id=? WHERE character_id=?",
                        (str(primary_target), self.character_id),
                    )
                self._snapshot(db, self.primary_target, now)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _snapshot(
        self, db: sqlite3.Connection, target: UUID, now: datetime
    ) -> AffectiveSnapshot:
        mood = Mood.model_validate_json(
            db.execute(
                "SELECT payload FROM affective_state WHERE character_id=?",
                (self.character_id,),
            ).fetchone()[0]
        ).recovered(now)
        row = db.execute(
            "SELECT payload FROM relationship_states WHERE character_id=? AND target_id=?",
            (self.character_id, str(target)),
        ).fetchone()
        relationship = (
            RelationshipState.model_validate_json(row[0])
            if row
            else RelationshipState.initial(
                target, mood.updated_at, target == self.primary_target
            )
        )
        if row is None:
            db.execute(
                "INSERT INTO relationship_states VALUES (?,?,?)",
                (self.character_id, str(target), relationship.model_dump_json()),
            )
        at = max(now, mood.updated_at, relationship.updated_at)
        emotions = tuple(
            EmotionEvent.model_validate_json(row[0])
            for row in db.execute(
                "SELECT payload FROM emotion_events WHERE character_id=? AND decay_until>? ORDER BY decay_until DESC LIMIT 24",
                (self.character_id, at.isoformat()),
            )
        )
        return AffectiveSnapshot(
            mood=mood, relationship=relationship, emotions=emotions, timestamp=at
        )

    def snapshot(self, target: UUID, now: datetime) -> AffectiveSnapshot:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return self._snapshot(db, target, now)

    def apply(self, event: Event, appraisal: AffectiveAppraisal) -> AffectiveSnapshot:
        if str(event.target) != self.character_id:
            raise ValueError("Event belongs to another Character")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            relation_target = (
                event.actor
                if event.type in {"conversation_message", "user_action"}
                else self.primary_target
            )
            before = self._snapshot(db, relation_target, event.timestamp)
            at = before.timestamp
            duplicate = db.execute(
                "SELECT 1 FROM affective_receipts WHERE character_id=? AND source_event_id=?",
                (self.character_id, str(event.id)),
            ).fetchone()
            if duplicate:
                return before
            # Whole update and receipt are atomic; no partial relationship/affect writes.
            db.execute(
                "INSERT INTO affective_receipts VALUES (?,?,?)",
                (self.character_id, str(event.id), at.isoformat()),
            )
            db.execute(
                "DELETE FROM emotion_events WHERE character_id=? AND decay_until<=?",
                (self.character_id, at.isoformat()),
            )
            impulses = {
                k: min(0.8, v * appraisal.appraisal.relevance)
                for k, v in appraisal.emotion_impulses.items()
                if v * appraisal.appraisal.relevance >= 0.05
            }
            for kind, strength in impulses.items():
                emotion = EmotionEvent(
                    type=kind,
                    intensity=strength,
                    target=event.actor,
                    cause_summary=appraisal.cause_summary,
                    created_at=at,
                    decay_until=at
                    + timedelta(minutes=20 + 100 * appraisal.event_significance),
                    source_event_id=event.id,
                )
                db.execute(
                    "INSERT INTO emotion_events VALUES (?,?,?,?,?)",
                    (
                        self.character_id,
                        str(event.id),
                        kind,
                        emotion.decay_until.isoformat(),
                        emotion.model_dump_json(),
                    ),
                )
            # Keep a bounded active working set; raw conversation remains in Archive.
            db.execute(
                "DELETE FROM emotion_events WHERE character_id=? AND rowid NOT IN (SELECT rowid FROM emotion_events WHERE character_id=? ORDER BY decay_until DESC, rowid DESC LIMIT 24)",
                (self.character_id, self.character_id),
            )
            mood = apply_emotions(before.mood, impulses)
            db.execute(
                "UPDATE affective_state SET payload=? WHERE character_id=?",
                (mood.model_dump_json(), self.character_id),
            )
            changes = (
                relationship_changes(appraisal)
                if event.type in {"conversation_message", "user_action"}
                and str(event.actor) != self.character_id
                else {}
            )
            if changes:
                row = db.execute(
                    "SELECT day,spent FROM relationship_budgets WHERE character_id=? AND target_id=?",
                    (self.character_id, str(event.actor)),
                ).fetchone()
                today = at.date().isoformat()
                day = max(today, row[0]) if row else today
                spent = json.loads(row[1]) if row and row[0] == day else {}
                # Absolute daily movement: opposite signals cannot refund the budget.
                if "comfort" in changes:
                    changes["formality"] = -changes["comfort"] * 0.5
                values = before.relationship.model_dump()
                for dimension, proposed in changes.items():
                    amount = min(abs(proposed), max(0, 0.03 - spent.get(dimension, 0)))
                    old = values[dimension]
                    values[dimension] = max(
                        0, min(1, old + (amount if proposed > 0 else -amount))
                    )
                    spent[dimension] = spent.get(dimension, 0) + abs(
                        values[dimension] - old
                    )
                values["stage"] = (
                    "close"
                    if min(values["trust"], values["closeness"]) >= 0.85
                    and values["familiarity"] >= 0.88
                    else "familiar"
                    if values["familiarity"] >= 0.65
                    and values["closeness"] >= 0.55
                    and values["trust"] >= 0.4
                    else "acquainted"
                    if values["familiarity"] >= 0.25
                    else "stranger"
                )
                values["updated_at"] = at
                updated = RelationshipState.model_validate(values)
                db.execute(
                    "UPDATE relationship_states SET payload=? WHERE character_id=? AND target_id=?",
                    (updated.model_dump_json(), self.character_id, str(event.actor)),
                )
                db.execute(
                    "INSERT OR REPLACE INTO relationship_budgets VALUES (?,?,?,?)",
                    (self.character_id, str(event.actor), day, json.dumps(spent)),
                )
                interaction = InteractionEvent(
                    event_type=appraisal.appraisal.social_meaning,
                    significance=appraisal.event_significance,
                    valence=appraisal.appraisal.valence,
                    target=event.actor,
                    summary=appraisal.cause_summary,
                    relationship_signals=appraisal.relationship_signal,
                    created_at=at,
                    source_event_id=event.id,
                )
                db.execute(
                    "INSERT INTO interaction_events VALUES (?,?,?)",
                    (self.character_id, str(event.id), interaction.model_dump_json()),
                )
            after = self._snapshot(db, relation_target, at)
            # This turn uses pre-update relationship; changes influence the next turn.
            return after.model_copy(update={"relationship": before.relationship})
