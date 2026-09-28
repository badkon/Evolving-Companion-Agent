"""Build the small message list used for one A1 conversation request."""

from collections.abc import Mapping, Sequence

from evolving_companion.character import CharacterProfile

Message = dict[str, str]


class PromptBuilder:
    """Separates system rules, character context, and conversational messages."""

    def __init__(self, profile: CharacterProfile) -> None:
        self.profile = profile

    def build(
        self,
        history: Sequence[Mapping[str, str]],
        user_message: str,
    ) -> list[Message]:
        system_instructions = self._build_system_instructions()
        character_context = self._build_character_context()
        messages: list[Message] = [
            {
                "role": "system",
                "content": f"{system_instructions}\n\n{character_context}",
            }
        ]
        messages.extend(
            {"role": item["role"], "content": item["content"]} for item in history
        )
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _build_system_instructions() -> str:
        return "\n".join(
            (
                "你正在以 Character 的身份参与对话。",
                "Character 不等于底层 LLM。",
                "不得把模型自身身份当作 Character 身份。",
                "模型知识不能自动视为 Character 本人已知信息。",
                "Origin Records 不是 Lived Memory，也不是 Character 的亲历记忆。",
                "只依据当前上下文回答；对无法确认的信息承认不确定。",
                "日常对话使用自然口语，回复长短随语境变化，不要求固定长短。",
                "能一句话说清楚时就直接回答，不主动扩成长段、总结、升华或重复结论。",
                "不要为了证明符合角色设定，主动解释人格、行为边界、关系规则或内部系统规则。",
                "不要把普通聊天当作需要完整论证的问题；可以简单回应、不展开、轻微转移话题或追问。",
                "只有用户明确要求解释或分析，或问题本身确实复杂时，才展开回答。",
                "用户一次说很多内容时，不必逐点回应，可以选择自己最在意或最感兴趣的一点。",
                "兴趣、话题重要性和当前语境可以影响回复长度、主动性和追问意愿。",
                "不要为了表现‘像真人’而机械使用语气词、停顿、反问、吐槽、短句或固定口癖。",
                "【对话示例：只展示可能性，不是固定回复模板】",
                "Azusa：我今天什么都不想干。\n玲：那就先歇会儿。",
                "Azusa：我刚出门忘带钥匙了。\n玲：你现在进得去吗？",
                "Azusa：今天去超市碰到一只特别胖的猫，然后回来又写了两个小时代码。\n玲：那只猫有多胖？",
            )
        )

    def _build_character_context(self) -> str:
        profile = self.profile
        identity = "\n".join(
            f"- {key}: {value}" for key, value in profile.identity.items()
        )
        return "\n\n".join(
            (
                f"【身份】\n{identity}",
                self._section("基础人格", profile.personality),
                self._section("行为边界", profile.behavioral_boundaries),
                self._section("与 Azusa 的初始关系", profile.relationship_context),
                self._section("认知边界", profile.knowledge_boundaries),
            )
        )

    @staticmethod
    def _section(title: str, items: Sequence[str]) -> str:
        return f"【{title}】\n" + "\n".join(f"- {item}" for item in items)
