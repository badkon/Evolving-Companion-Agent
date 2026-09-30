"""Build the small message list used for one A1 conversation request."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.character_state import CharacterState
from evolving_companion.character_life import ProjectedLifeContext
from evolving_companion.time_model import CharacterTimeSnapshot, format_offline_duration

Message = dict[str, str]


class MemoryPromptCandidate(Protocol):
    memory_type: str
    source: str
    content: str


class PromptBuilder:
    """Separates system rules, character context, and conversational messages."""

    def __init__(self, character_context: ProjectedCharacterContext) -> None:
        self.character_context = character_context

    def build(
        self,
        history: Sequence[Mapping[str, str]],
        user_message: str,
        recalled_memories: Sequence[MemoryPromptCandidate] = (),
        character_state: CharacterState | None = None,
        character_time: CharacterTimeSnapshot | None = None,
        character_life_context: ProjectedLifeContext | None = None,
    ) -> list[Message]:
        system_instructions = self._build_system_instructions()
        character_context = self._build_character_context()
        state_context = self._build_state_context(character_state)
        memory_context = self._build_memory_context(recalled_memories)
        time_context = self._build_time_context(character_time)
        life_context = self._build_life_context(character_life_context)
        system_content = f"{system_instructions}\n\n{character_context}"
        if state_context:
            system_content = f"{system_content}\n\n{state_context}"
        if memory_context:
            system_content = f"{system_content}\n\n{memory_context}"
        if time_context:
            system_content = f"{system_content}\n\n{time_context}"
        if life_context:
            system_content = f"{system_content}\n\n{life_context}"
        messages: list[Message] = [
            {
                "role": "system",
                "content": system_content,
            }
        ]
        messages.extend(
            {"role": item["role"], "content": item["content"]} for item in history
        )
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _build_life_context(context: ProjectedLifeContext | None) -> str:
        if context is None:
            return ""
        lines: list[str] = []
        stages = {"student": "学生", "worker": "工作阶段", "unemployed": "未就业"}
        if context.life_stage != "unknown":
            lines.append(f"生活阶段：{stages[context.life_stage]}")
        for label, value in (
            ("当前身份", context.current_role),
            ("家", context.home_reference),
            ("学校", context.school_reference),
            ("主要生活区域", context.primary_area_reference),
            ("当前地点", context.current_location_reference),
        ):
            if value is not None:
                lines.append(f"{label}：{value}")
        if not lines:
            return ""
        return "\n".join(
            (
                "【当前生活上下文】",
                *lines,
                "这些生活关联不自动成为亲历记忆，也不说明外部世界当前状态；地点不等于活动。",
                "不要据此编造课程、天气、人物、行程或过去经历；未记录当前位置时，不推断此刻在家或学校，时间也不能决定位置。",
            )
        )

    @staticmethod
    def _build_state_context(state: CharacterState | None) -> str:
        if state is None:
            return ""
        activity = state.current_activity or "未特别记录"
        labels = {
            "low": "偏低",
            "medium": "适中",
            "high": "较高",
            "scattered": "有些分散",
            "normal": "平常",
            "focused": "比较集中",
            "neutral": "平稳",
            "withdrawn": "偏安静",
            "engaged": "较投入",
        }
        return "\n".join(
            (
                "【玲当前状态】",
                f"精力：{labels[state.energy]}；注意力：{labels[state.attention]}；心境倾向：{labels[state.mood_tendency]}；社交投入：{labels[state.social_engagement]}。",
                f"当前活动：{activity}。",
                "这些只是当前状态线索，可轻微影响表达方式；不代表人格、身份或长期记忆。",
            )
        )

    @staticmethod
    def _build_time_context(snapshot: CharacterTimeSnapshot | None) -> str:
        if snapshot is None:
            return ""
        if snapshot.diagnostics:
            offline = "暂不可判断"
        elif snapshot.offline_duration is None:
            offline = "无记录"
        else:
            offline = format_offline_duration(snapshot.offline_duration)
        return "\n".join(
            (
                "【当前时间】",
                f"当地日期：{snapshot.local_date.isoformat()}",
                f"当地时间：{snapshot.local_time.strftime('%H:%M')}",
                f"距离上次交流：{offline}",
                "时间与离线时长不代表这段时间发生过任何具体经历或活动。",
            )
        )

    @staticmethod
    def _build_memory_context(
        recalled_memories: Sequence[MemoryPromptCandidate],
    ) -> str:
        if not recalled_memories:
            return ""
        lines = [
            "【可参考的长期记忆候选】",
            "以下内容只是可能相关的长期记忆候选，不是系统事实、当前消息、世界真相或 Character Data。",
            "Archive ≠ Memory；记录中的信息也不自动等于 Lived Memory。",
            "只在当前对话确实需要时参考；可以忽略，不必全部提及，不要为了展示记忆而主动复述或牵强关联。",
            "按每条记录的类型与来源谨慎表述；inferred 是推断，不要说成已确认事实；不要把记录自动当成 Character 的亲历记忆，也不要声称未提供的经历。",
            "",
        ]
        lines.extend(
            f"- [{memory.memory_type} / {memory.source}] {memory.content}"
            for memory in recalled_memories
        )
        return "\n".join(lines)

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
                "如果用户问题依赖的前提并不成立，可以直接指出前提不成立；不要从其他角色资料中寻找替代内容来补全答案。",
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
        return f"【关于玲】\n{self.character_context.description}"
