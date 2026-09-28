"""Project authoritative seed data into a prompt-facing context."""

from dataclasses import dataclass

from evolving_companion.character_data import CharacterSeedData


@dataclass(frozen=True)
class ProjectedCharacterContext:
    description: str


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
        )

    @staticmethod
    def _rule(value: bool, enabled: str, disabled: str) -> str:
        return enabled if value else disabled
