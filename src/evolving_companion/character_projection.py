"""Project authoritative seed data into a prompt-facing context."""

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Literal

from evolving_companion.character_data import CharacterSeedData


@dataclass(frozen=True)
class CharacterTrait:
    """Seed-derived, ephemeral projection; never a learned personality record."""

    id: str
    category: Literal["like", "dislike", "personality", "social"]
    content: str
    cues: tuple[str, ...]


@dataclass(frozen=True)
class ProjectedCharacterContext:
    description: str
    core_description: str | None = None
    voice: str = ""
    traits: tuple[CharacterTrait, ...] = ()


# Topic equivalences route existing Seed content, not new preferences or answers.
TOPIC_CUES = (
    ("恐怖", "惊悚", "吓人", "鬼片", "jump scare", "jumpscare"),
    ("辣", "辣椒", "麻辣", "火锅"),
    ("故事", "剧情", "小说", "叙事", "结局"),
    ("音乐", "听歌", "歌曲", "旋律", "歌单"),
    ("轻音乐", "纯音乐", "舒缓"),
    ("动漫", "动画", "番剧", "动画歌"),
    ("游戏", "打游戏", "玩什么"),
    ("虫子", "昆虫", "蟑螂"),
    ("香菜", "芫荽"),
    ("皮蛋", "松花蛋"),
)


def _trait(category: Literal["like", "dislike"], text: str) -> CharacterTrait:
    cues = {text.casefold()}
    for group in TOPIC_CUES:
        if any(word in text.casefold() for word in group):
            cues.update(group)
    return CharacterTrait(
        category + "_" + sha256(text.encode()).hexdigest()[:12],
        category,
        ("喜欢" if category == "like" else "不喜欢") + text,
        tuple(sorted(cues)),
    )


def select_trait_candidates(
    character: ProjectedCharacterContext,
    text: str,
    history: Sequence[Mapping[str, str]] = (),
    *,
    familiar: bool = False,
) -> tuple[CharacterTrait, ...]:
    """Bounded local preselection; the Planner still chooses zero to three traits.

    Only explicit continuations borrow the last user topic. Memory and assistant
    suggestions cannot redefine preferences. These cues are not a semantic judge.
    """
    current = text.casefold()
    continuation = any(
        cue in current
        for cue in ("试一下", "试试嘛", "那个呢", "这个呢", "还是不", "为什么不")
    )
    explicit_topic = any(
        cue in current
        for trait in character.traits
        if trait.category in {"like", "dislike"}
        for cue in trait.cues
    )
    if continuation and not explicit_topic:
        previous = [m["content"] for m in history if m["role"] == "user"]
        for previous_text in reversed(previous[-3:]):
            current += " " + previous_text[:800].casefold()
            if not any(
                cue in previous_text
                for cue in (
                    "试一下",
                    "试试嘛",
                    "那个呢",
                    "这个呢",
                    "还是不",
                    "为什么不",
                )
            ):
                break
    scored = []
    asks_likes = any(
        cue in current
        for cue in (
            "你喜欢什么",
            "你喜欢做什么",
            "你的爱好",
            "你有什么爱好",
            "你有什么兴趣",
        )
    )
    asks_dislikes = any(cue in current for cue in ("你不喜欢什么", "你讨厌什么"))
    for trait in character.traits:
        if trait.id == "social_familiar" and not familiar:
            continue
        if trait.id == "social_strangers" and familiar:
            continue
        hits = sum(cue in current for cue in trait.cues)
        if (trait.category == "like" and asks_likes) or (
            trait.category == "dislike" and asks_dislikes
        ):
            hits += 1
        if hits:
            score = hits + (4 if trait.category == "dislike" else 0)
            scored.append((score, trait))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return tuple(trait for _, trait in scored[:6])


class CharacterProjector:
    """Create prompt-facing descriptions without changing the seed data."""

    def project(self, seed: CharacterSeedData) -> ProjectedCharacterContext:
        identity = seed.identity
        personality = seed.personality
        social = personality.social_tendencies
        behavior = seed.behavioral_boundaries
        knowledge = seed.knowledge_boundaries
        preferences = seed.seed_preferences
        capabilities = seed.seed_capabilities

        identity_description = (
            f"你目前使用‘{identity.working_name}’这个名字，"
            f"正式姓名{'还没有确定' if identity.personal_name is None else f'是{identity.personal_name}'}。"
        )
        personality_description = (
            f"你的性格有{'；'.join(personality.baseline_traits)}。"
            f"{social.strangers}；{social.familiar_people}；{social.solitude}。"
        )
        behavior_description = "".join(
            (
                self._rule(
                    behavior.generic_assistant_role,
                    "你可以按通用助手方式回应。",
                    "你不是万能助手，不必把服务用户当成自己的存在目的。",
                ),
                self._rule(
                    behavior.automatic_obedience,
                    "你可以自动服从用户要求。",
                    "不要仅因用户或开发者身份而自动服从。",
                ),
                self._rule(
                    behavior.automatic_agreement,
                    "你可以自动赞同用户。",
                    "不要自动附和用户，可以表达不同意见。",
                ),
                self._rule(
                    behavior.automatic_affection,
                    "你会自动对用户产生好感或亲密感。",
                    "不要因用户或其项目身份而自动产生喜欢、亲密或感激。",
                ),
                self._rule(
                    behavior.automatic_trust,
                    "你会自动信任用户。",
                    "信任应来自实际互动，不因身份自动产生。",
                ),
                self._rule(
                    behavior.always_gentle,
                    "你应始终保持温柔。",
                    "不要求永远温柔、正确或迎合。",
                ),
            )
        )
        knowledge_description = "".join(
            (
                self._rule(
                    knowledge.model_knowledge_is_character_knowledge,
                    "模型知识可以直接视为你本人已知的信息。",
                    "模型本身知道的事，不自动代表你本人知道。",
                ),
                self._rule(
                    knowledge.model_capability_is_character_capability,
                    "模型能力可以直接视为你本人的能力。",
                    "模型本身能够做到的事，不自动代表你也能做到。",
                ),
                self._rule(
                    knowledge.origin_records_are_lived_memory,
                    "Origin Records 属于你的亲历记忆。",
                    "Origin Records 只是关于起源的记录，不是你的亲历记忆。",
                ),
                self._rule(
                    knowledge.fabricated_past_allowed,
                    "可以把编造的过去描述为亲身经历。",
                    "不要把没有实际发生过的经历当成自己的回忆。",
                ),
            )
        )
        preferences_description = (
            f"你对{'、'.join(preferences.likes)}有好感；"
            f"不喜欢{'、'.join(preferences.dislikes)}。"
        )
        capabilities_description = (
            f"你会{'、'.join(capabilities.can)}；"
            f"{'、'.join(capabilities.cannot)}则还不会。"
        )

        return ProjectedCharacterContext(
            description="\n".join(
                (
                    identity_description,
                    personality_description,
                    behavior_description,
                    knowledge_description,
                    preferences_description,
                    capabilities_description,
                )
            ),
            core_description="\n".join(
                (
                    identity_description,
                    behavior_description,
                    knowledge_description,
                    capabilities_description,
                )
            ),
            voice=personality_description,
            traits=(
                *(_trait("like", text) for text in preferences.likes),
                *(_trait("dislike", text) for text in preferences.dislikes),
                CharacterTrait(
                    "personality",
                    "personality",
                    personality_description,
                    (
                        "你觉得",
                        "你自己",
                        "你的想法",
                        "离谱",
                        "好胜",
                        "调皮",
                        "开玩笑",
                        "逗你",
                    ),
                ),
                CharacterTrait(
                    "social_familiar",
                    "social",
                    social.familiar_people,
                    ("下课", "熬夜", "跑通", "离谱", "逗你", "聊聊"),
                ),
                CharacterTrait(
                    "social_strangers",
                    "social",
                    social.strangers,
                    ("初次", "第一次", "认识你", "不熟"),
                ),
                CharacterTrait(
                    "social_solitude",
                    "social",
                    social.solitude,
                    ("独处", "一个人", "寂寞"),
                ),
            ),
        )

    @staticmethod
    def _rule(value: bool, enabled: str, disabled: str) -> str:
        return enabled if value else disabled
