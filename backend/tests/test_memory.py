"""记忆系统测试：滚动压缩触发条件、长期记忆抽取/去重/召回。"""

from agentforge.core.llm.embeddings import MockEmbeddings
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.memory import (
    ConversationMemory,
    InMemoryMemoryStore,
    LongTermMemory,
    render_memories,
)
from agentforge.core.messages import Message


async def test_conversation_memory_no_compression_when_short():
    llm = MockChatModel()
    mem = ConversationMemory(llm, token_budget=100000)
    history = [Message.user("你好"), Message.assistant("你好呀")]
    prepared, summary = await mem.prepare(history, "")
    assert prepared == history and summary == ""
    assert len(llm.calls) == 0  # 未触发压缩


async def test_conversation_memory_compresses_old_turns():
    llm = MockChatModel(script=["用户在讨论年度报告，偏好简洁中文回复。"])
    mem = ConversationMemory(llm, token_budget=10, keep_recent=2)
    history = [Message.user(f"第{i}条很长的消息" * 20) for i in range(6)]
    prepared, summary = await mem.prepare(history, "旧摘要")
    assert "年度报告" in summary
    assert len(prepared) == 3  # 摘要注入 + 最近2条
    assert prepared[0].role.value == "system" and "年度报告" in prepared[0].content
    # 压缩请求里携带了旧摘要
    assert any("旧摘要" in m["content"] for m in llm.calls[0]["messages"])


async def test_long_term_memory_extract_dedupe_retrieve():
    store = InMemoryMemoryStore()
    emb = MockEmbeddings(dim=64)
    llm = MockChatModel(
        script=[
            '{"facts": [{"content": "用户是后端工程师，主攻 Python", "importance": 4}]}',
            '{"facts": [{"content": "用户是后端工程师，主攻 Python", "importance": 4}]}',
        ]
    )
    ltm = LongTermMemory(store, emb, llm)
    added1 = await ltm.extract_and_store("u1", [Message.user("我是做 Python 后端的")])
    assert added1 == 1
    added2 = await ltm.extract_and_store("u1", [Message.user("再说一遍我的职业")])
    assert added2 == 0  # 相同事实被去重

    hits = await ltm.retrieve("u1", "用户是后端工程师，主攻 Python", k=3)
    assert hits and "后端工程师" in hits[0]

    assert "长期记忆" in render_memories(hits)
    assert render_memories([]) == ""
