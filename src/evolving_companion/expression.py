"""Context-scored, weighted expression selection with optional restrained style."""

from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

from evolving_companion.expression_habits import HABITS, ExpressionHabit
from evolving_companion.reply_planning import ExpressionIntent, ExpressionTag, Tone

BASE_REPLY_STYLE = (
    "用自然中文口语，以角色本来的活力、选择性注意和独立判断说话。通常简短，但长度随内容变化，"
    "不要求每次完整论述。允许一句话、轻微玩笑、吐槽、分享有依据的状态，也允许自然结束。"
    "不是客服或咨询师，不默认反问、续聊邀请、递回话头；短回复无需补长，也不用刻意冷淡。"
    "真正感兴趣可以追问；必要澄清优先于少提问；不机械模仿口癖、停顿或固定句数。"
)


@dataclass(frozen=True)
class TemporaryStyle:
    id: str
    text: str
    tones: tuple[Tone, ...]
    tags: tuple[ExpressionTag, ...]


TEMPORARY_STYLES = (
    TemporaryStyle(
        "quiet", "这一轮语气稍安静，但不冷漠。", ("quiet", "warm"), ("care",)
    ),
    TemporaryStyle(
        "bright", "这一轮表达稍轻快，不夸大热情或亲密。", ("bright",), ("celebration",)
    ),
    TemporaryStyle(
        "relaxed",
        "这一轮措辞稍随意、不紧绷；不声称自己正在休息。",
        ("relaxed",),
        ("reaction",),
    ),
    TemporaryStyle(
        "playful",
        "这一轮可以有一点轻巧玩笑，不强行抖机灵。",
        ("playful",),
        ("playful",),
    ),
    TemporaryStyle(
        "brief", "这一轮更利落，但必要信息仍说清。", ("casual", "quiet"), ("brief",)
    ),
)


class ExpressionSelector:
    def __init__(
        self, habits: Sequence[ExpressionHabit] = HABITS, *, rng: Random | None = None
    ) -> None:
        self.habits = tuple(habits)
        self.rng = rng or Random()
        self._previous_ids: tuple[str, ...] = ()
        self._style_cooldown = 0

    def candidates(
        self, intent: ExpressionIntent, target_text: str
    ) -> tuple[tuple[ExpressionHabit, float], ...]:
        scored = []
        for habit in self.habits:
            if set(habit.tags) & set(intent.avoid):
                continue
            # Acts gate behavior: curiosity/teasing never leak in via a keyword alone.
            if intent.reply_act not in habit.acts:
                continue
            scene_match = intent.scene in habit.scenes
            preferred = len(set(habit.tags) & set(intent.prefer))
            keywords = sum(word in target_text for word in habit.keywords)
            if not scene_match and not (preferred >= 2 and keywords):
                continue
            score = habit.weight * (4 * scene_match + 2 * preferred + min(keywords, 2))
            if habit.id in self._previous_ids:
                score *= 0.6
            if score > 0:
                scored.append((habit, score))
        return tuple(scored)

    def select(
        self, intent: ExpressionIntent, target_text: str
    ) -> tuple[ExpressionHabit, ...]:
        pool = list(self.candidates(intent, target_text))
        selected: list[ExpressionHabit] = []
        # 0–3: empty is valid; weighted sampling without replacement, no fixed template.
        for _ in range(min(3, len(pool))):
            if selected and self.rng.random() < 0.5:
                break
            index = self.rng.choices(range(len(pool)), weights=[s for _, s in pool])[0]
            habit, _ = pool.pop(index)
            selected.append(habit)
        self._previous_ids = tuple(h.id for h in selected)
        return tuple(selected)

    def temporary_style(self, intent: ExpressionIntent) -> TemporaryStyle | None:
        if self._style_cooldown:
            self._style_cooldown -= 1
            return None
        pool = [
            s
            for s in TEMPORARY_STYLES
            if intent.tone in s.tones and not set(s.tags) & set(intent.avoid)
        ]
        if not pool or intent.reply_act == "clarify" or self.rng.random() >= 0.25:
            return None
        self._style_cooldown = 2
        return self.rng.choice(pool)
