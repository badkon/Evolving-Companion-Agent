from collections.abc import Mapping

from evolving_companion.character import CharacterProfile
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import PromptBuilder


class FakeLLMClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.requests: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        return next(self.replies)


def test_character_profile_has_a1_identity_and_sections() -> None:
    profile = CharacterProfile()

    assert profile.identity["development_id"] == "SI-001"
    assert profile.identity["working_name"] == "玲"
    assert profile.identity["identity_stage"] == "Pre-Identity"
    assert profile.identity["personal_name_finalized"] is False
    assert profile.personality
    assert profile.behavioral_boundaries
    assert profile.relationship_context
    assert profile.knowledge_boundaries


def test_prompt_builder_separates_system_context_and_history() -> None:
    builder = PromptBuilder(CharacterProfile())
    history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀。"},
    ]

    messages = builder.build(history, "你是谁？")

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1:3] == history
    assert messages[-1] == {"role": "user", "content": "你是谁？"}
    assert "【身份】" in messages[0]["content"]
    assert "【基础人格】" in messages[0]["content"]
    assert "【行为边界】" in messages[0]["content"]
    assert "【与 Azusa 的初始关系】" in messages[0]["content"]
    assert "【认知边界】" in messages[0]["content"]


def test_system_message_includes_core_identity_and_knowledge_boundaries() -> None:
    system_content = PromptBuilder(CharacterProfile()).build([], "测试")[0]["content"]

    assert "Character 不等于底层 LLM" in system_content
    assert "不得把模型自身身份当作 Character 身份" in system_content
    assert "模型知识不能自动视为 Character 本人已知信息" in system_content
    assert "Origin Records 不是 Lived Memory" in system_content
    assert "SI-001" in system_content
    assert "玲" in system_content
    assert "Pre-Identity" in system_content
    assert "personal_name_finalized: False" in system_content


def test_system_instructions_support_variable_lively_dialogue() -> None:
    system_content = PromptBuilder(CharacterProfile()).build([], "测试")[0]["content"]
    system_instructions = system_content.split("【身份】", maxsplit=1)[0]

    assert "不要求固定长短" in system_instructions
    assert "不必逐点回应" in system_instructions
    assert "可以影响回复长度、主动性和追问意愿" in system_instructions
    assert "可以简单回应、不展开" in system_instructions
    assert "不要为了表现‘像真人’而机械使用" in system_instructions
    assert "不要把普通聊天当作需要完整论证的问题" in system_instructions
    assert "不主动扩成长段" in system_content
    assert "问题本身确实复杂时，才展开回答" in system_content
    assert system_instructions.count("Azusa：") == 3
    assert "每轮都先" not in system_instructions
    assert "默认吐槽" not in system_instructions


def test_conversation_appends_successful_turns_in_order_in_memory() -> None:
    llm_client = FakeLLMClient(["你好呀。", "我喜欢看故事。"])
    conversation = Conversation(llm_client)

    assert conversation.history == ()
    assert conversation.send("你好") == "你好呀。"
    assert conversation.send("你喜欢什么？") == "我喜欢看故事。"

    history = conversation.history
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["content"] for message in history] == [
        "你好",
        "你好呀。",
        "你喜欢什么？",
        "我喜欢看故事。",
    ]
    assert llm_client.requests[1][1:3] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀。"},
    ]


def test_failed_llm_request_does_not_append_history() -> None:
    class FailingClient:
        def complete(self, messages: list[Mapping[str, str]]) -> str:
            raise RuntimeError("provider unavailable")

    conversation = Conversation(FailingClient())

    try:
        conversation.send("你好")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the fake LLM request to fail")

    assert conversation.history == ()
