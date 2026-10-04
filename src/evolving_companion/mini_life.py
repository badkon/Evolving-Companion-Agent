"""Read-only coarse virtual routine; not executed events or persistent Life State."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from evolving_companion.character_data import CharacterSeedData
from evolving_companion.character_life import ProjectedLifeContext

# Local minutes since midnight; all schedule thresholds live here.
# Character Design: school ends around 14:00; rest-day waking around 08:00.
SCHEDULES = {
    "weekday": (
        (0, "late_night", "sleeping"),
        (420, "morning", "getting_ready"),
        (510, "morning", "school"),
        (720, "noon", "lunch"),
        (780, "noon", "school"),
        (840, "afternoon", "free_time"),
        (1080, "evening", "free_time"),
        (1320, "late_night", "preparing_sleep"),
        (1410, "late_night", "sleeping"),
    ),
    "weekend": (
        (0, "late_night", "sleeping"),
        (480, "morning", "getting_ready"),
        (570, "morning", "free_time"),
        (720, "noon", "lunch"),
        (840, "afternoon", "free_time"),
        (1080, "evening", "free_time"),
        (1320, "late_night", "preparing_sleep"),
        (1410, "late_night", "sleeping"),
    ),
}
ACTIVITY_LABELS = {
    "sleeping": "休息睡觉",
    "getting_ready": "洗漱、准备一天",
    "school": "学校安排",
    "lunch": "午饭与休息",
    "free_time": "自由活动",
    "reading": "读故事",
    "gaming": "玩故事向游戏",
    "listening_music": "听音乐",
    "relaxing": "放松",
    "preparing_sleep": "准备睡觉",
}


@dataclass(frozen=True)
class MiniLifeContext:
    # 推导的虚拟生活安排，不是实际执行记录，也不是现实位置传感器。
    now: dict[str, str]
    next: dict[str, str]
    today: dict[str, str]

    def summary(self) -> dict[str, str]:
        location = {"home": "家里", "school": "学校", "unknown": "位置未定"}
        return {
            "now": f"{location[self.now['location']]}，{ACTIVITY_LABELS[self.now['activity']]}",
            "next": f"{self.next['time_hint']}：{ACTIVITY_LABELS[self.next['activity']]}",
            "today": (
                (
                    "周末，节奏轻松"
                    if self.today["schedule_type"] == "weekend"
                    else "工作日"
                )
                + (
                    "，上午有学校安排、14:00后自由时间"
                    if self.today["pace"] == "school_then_free"
                    else "，以个人时间为主"
                )
                + "；晚上个人时间，晚些准备休息；明天"
                + self.today["tomorrow_schedule"]
            ),
        }


class MiniLifeService:
    """Pure Seed/time derivation; no Clock, DB, model or background work."""

    def __init__(self, seed: CharacterSeedData) -> None:
        self.seed = seed
        self.timezone = ZoneInfo(seed.timezone)
        likes = seed.seed_preferences.likes
        self.free_activities = tuple(
            activity
            for activity, cue in (
                ("reading", "故事"),
                ("gaming", "游戏"),
                ("listening_music", "音乐"),
            )
            if any(cue in like for like in likes)
        ) or ("relaxing",)

    def build(
        self, now: datetime, life: ProjectedLifeContext | None = None
    ) -> MiniLifeContext:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Mini Life requires an aware datetime")
        local = now.astimezone(self.timezone)
        initial = self.seed.initial_life_context
        student = (
            life.life_stage == "student" and life.school_reference is not None
            if life is not None
            else initial.life_stage == "student"
            and initial.school_entity_id is not None
        )
        home = (
            life.home_reference is not None
            if life is not None
            else initial.home_entity_id is not None
        )

        def resolve(day: datetime, block: tuple[int, str, str]) -> str:
            _, period, activity = block
            if activity == "school" and not student:
                activity = "free_time"
            if activity == "free_time":
                key = f"{self.seed.identity.internal_id}:{day.date()}:{period}"
                index = int.from_bytes(sha256(key.encode()).digest()[:8], "big")
                return self.free_activities[index % len(self.free_activities)]
            return activity

        kind = "weekend" if local.weekday() >= 5 else "weekday"
        blocks = SCHEDULES[kind]
        minute = local.hour * 60 + local.minute
        index = max(i for i, block in enumerate(blocks) if block[0] <= minute)
        activity = resolve(local, blocks[index])
        next_day = local
        if index + 1 < len(blocks):
            next_block = blocks[index + 1]
        else:
            next_day = local + timedelta(days=1)
            next_kind = "weekend" if next_day.weekday() >= 5 else "weekday"
            next_block = SCHEDULES[next_kind][1]  # Next waking block, not midnight.
        tomorrow_school = student and (local + timedelta(days=1)).weekday() < 5
        return MiniLifeContext(
            now=dict(
                location="school"
                if activity == "school"
                else "home"
                if home
                else "unknown",
                activity=activity,
            ),
            next=dict(
                activity=resolve(next_day, next_block),
                time_hint=("明天 " if next_day.date() != local.date() else "今天 ")
                + f"{next_block[0] // 60:02d}:{next_block[0] % 60:02d}左右",
            ),
            today=dict(
                pace="school_then_free" if student and kind == "weekday" else "relaxed",
                schedule_type=kind,
                tomorrow_schedule="通常有学校安排（无具体课表）"
                if tomorrow_school
                else "以自由时间为主",
            ),
        )
