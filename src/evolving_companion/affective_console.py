"""Read-only Console views over existing affective records; no initialization."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import UUID

from evolving_companion.affective import EmotionEvent, Mood, RelationshipState
from evolving_companion.clock import Clock, SystemClock

STAGES = {
    "stranger": "陌生",
    "acquainted": "认识",
    "familiar": "熟人",
    "close": "亲近",
}
RELATIONSHIP_LABELS = {
    "familiarity": "熟悉程度",
    "trust": "信任",
    "closeness": "亲近程度",
    "comfort": "相处舒适",
    "formality": "正式程度",
}
MOOD_LABELS = {
    "valence": "整体情绪",
    "energy": "精力",
    "calmness": "平静程度",
    "sociability": "交流意愿",
}
EMOTIONS = {
    "joy": "开心",
    "sadness": "难过",
    "anger": "生气",
    "annoyance": "烦躁",
    "fear": "害怕",
    "surprise": "惊讶",
    "curiosity": "好奇",
    "affection": "亲近感",
    "relief": "如释重负",
    "disappointment": "失望",
}


def mood_summary(mood: Mood) -> str:
    """Presentation labels only; recovery math stays in the production model."""
    parts = [
        "整体偏低落"
        if mood.valence < -0.15
        else "整体心情较好"
        if mood.valence > 0.3
        else "整体平稳",
        "精力略低"
        if mood.energy < -0.15
        else "精力较充足"
        if mood.energy > 0.3
        else "精力一般",
        "有些不平静"
        if mood.calmness < -0.1
        else "比较平静"
        if mood.calmness >= 0.2
        else "平静程度一般",
        "交流意愿较低"
        if mood.sociability < -0.1
        else "较愿意交流"
        if mood.sociability > 0.3
        else "交流意愿一般",
    ]
    return "，".join(parts) + "。"


def empty_affective(message: str, *, status: str = "empty") -> dict:
    return {
        "status": status,
        "message": message,
        "relationship": None,
        "mood": None,
        "emotions": [],
        "active_emotions": [],
        "active_count": 0,
        "debug": {},
    }


class AffectiveConsoleService:
    def __init__(
        self, database: Path, character_id: UUID, clock: Clock | None = None
    ) -> None:
        self.database = database
        self.character_id = str(character_id)
        self.clock = clock or SystemClock()

    def read(self) -> dict:
        if not self.database.is_file():
            return empty_affective("运行数据库尚未初始化。")
        now = self.clock.now_utc()
        # AffectiveStore construction/snapshot can write. Read its existing tables
        # directly and reuse only validated records and their pure time calculations.
        with closing(
            sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only = ON")
            db.set_progress_handler(lambda: 1, 2000000)
            db.execute("BEGIN")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('affective_state','relationship_states','emotion_events',"
                    "'affective_primary_target')"
                )
            }
            if not tables:
                return empty_affective("情绪与关系状态尚未初始化。")
            mood = None
            if "affective_state" in tables:
                row = db.execute(
                    "SELECT payload FROM affective_state WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()
                if row:
                    mood = Mood.model_validate_json(row[0])
            target = None
            if "affective_primary_target" in tables:
                row = db.execute(
                    "SELECT target_id FROM affective_primary_target WHERE character_id=?",
                    (self.character_id,),
                ).fetchone()
                if row:
                    target = UUID(row[0])
            relationship = None
            if target is not None and "relationship_states" in tables:
                row = db.execute(
                    "SELECT payload FROM relationship_states "
                    "WHERE character_id=? AND target_id=?",
                    (self.character_id, str(target)),
                ).fetchone()
                if row:
                    relationship = RelationshipState.model_validate_json(row[0])
                    if relationship.target != target:
                        raise ValueError("Relationship target mismatch")
            emotions = []
            if "emotion_events" in tables:
                emotions = [
                    EmotionEvent.model_validate_json(row[0])
                    for row in db.execute(
                        "SELECT payload FROM emotion_events WHERE character_id=? "
                        "ORDER BY rowid DESC LIMIT 24",
                        (self.character_id,),
                    )
                ]
        # Match runtime's clock rollback anchor without persisting recovery or decay.
        at = max(
            now,
            mood.updated_at if mood else now,
            relationship.updated_at if relationship else now,
        )
        result = empty_affective("只读状态；刷新时重新读取。", status="ready")
        if relationship:
            result["relationship"] = {
                "stage": relationship.stage,
                "stage_label": STAGES[relationship.stage],
                "summary": STAGES[relationship.stage]
                + (" · 相处放松" if relationship.comfort >= 0.6 else " · 相处仍有保留"),
                "target_label": "主要关系对象（运行时已绑定）",
                "romantic": relationship.romantic,
                "boundary": "关系边界：非恋爱关系",
                "updated_at": relationship.updated_at.isoformat(),
                "dimensions": [
                    {
                        "key": key,
                        "label": label,
                        "value": getattr(relationship, key),
                        "percent": round(getattr(relationship, key) * 100, 1),
                    }
                    for key, label in RELATIONSHIP_LABELS.items()
                ],
            }
        if mood:
            current = mood.recovered(now)
            result["mood"] = {
                "summary": mood_summary(current),
                "updated_at": mood.updated_at.isoformat(),
                "dimensions": [
                    {
                        "key": key,
                        "label": label,
                        "value": getattr(current, key),
                        "position": (getattr(current, key) + 1) * 50,
                    }
                    for key, label in MOOD_LABELS.items()
                ],
            }
        recent = sorted(emotions, key=lambda e: e.created_at, reverse=True)
        result["emotions"] = [self._emotion(e, at, target) for e in recent[:12]]
        active = sorted(
            (e for e in emotions if e.strength_at(at) > 0),
            key=lambda e: e.strength_at(at),
            reverse=True,
        )
        result["active_count"] = len(active)
        seen = set()
        for emotion in active:
            if emotion.type not in seen and len(seen) < 3:
                result["active_emotions"].append(
                    {
                        "label": EMOTIONS[emotion.type],
                        "intensity": emotion.strength_at(at),
                    }
                )
                seen.add(emotion.type)
        result["debug"] = {
            "as_of": at.isoformat(),
            "storage_source": "运行 SQLite · 只读查询",
            "primary_target": str(target) if target else "尚未绑定",
        }
        return result

    @staticmethod
    def _emotion(emotion: EmotionEvent, at: datetime, target: UUID | None) -> dict:
        strength = emotion.strength_at(at)
        return {
            "label": EMOTIONS[emotion.type],
            "type": emotion.type,
            "intensity": strength,
            "initial_intensity": emotion.intensity,
            "active": strength > 0,
            "cause_summary": emotion.cause_summary,
            "created_at": emotion.created_at.isoformat(),
            "decay_until": emotion.decay_until.isoformat(),
            "source_event_id": str(emotion.source_event_id),
            "target_label": "主要关系对象相关"
            if target == emotion.target
            else "其他对象相关（不自动归因于主要用户）",
        }
