"""增强功能测试：语义缓存、RAG 进阶（改写/HyDE/压缩/父子分块）、内置工具、自定义工具。"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from agentforge.core.llm.embeddings import MockEmbeddings
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.tools.builtins import calculator, current_time
from agentforge.db.base import Base, build_engine
from agentforge.db.models import Chunk, CustomTool, Document, KnowledgeBase, User
from agentforge.rag.enhance import compress_context, hyde_document, rewrite_query
from agentforge.rag.pipeline import RagOptions, RagPipeline
from agentforge.rag.retriever import HybridRetriever
from agentforge.rag.tokenize import tokenize
from agentforge.services.custom_tools import build_custom_tool
from agentforge.services.semantic_cache import SemanticCache, scope_key

# ---------- 内置工具 ----------


async def test_calculator():
    r = await calculator.execute({"expression": "(1234 * 56 + 78) / 2"})
    assert r.ok and "34591" in r.content  # (69104 + 78) / 2
    bad = await calculator.execute({"expression": "__import__('os')"})
    assert not bad.ok
    zero = await calculator.execute({"expression": "1/0"})
    assert not zero.ok and "零" in zero.content


async def test_current_time():
    r = await current_time.execute({"tz_offset_hours": 8})
    assert r.ok and "UTC+8" in r.content


# ---------- 语义缓存 ----------


async def _sessions(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/cache.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def test_scope_key_isolation():
    assert scope_key("assistant", ["kb1"]) != scope_key("team", ["kb1"])
    assert scope_key("assistant", ["a", "b"]) == scope_key("assistant", ["b", "a"])  # 顺序无关
    assert scope_key("assistant", [], user_id="u1") != scope_key("assistant", [], user_id="u2")
    assert scope_key("assistant", ["kb1"], revision="v1") != scope_key(
        "assistant", ["kb1"], revision="v2"
    )


async def test_semantic_cache_hit_and_miss(tmp_path):
    sessions, engine = await _sessions(tmp_path)
    cache = SemanticCache(sessions, MockEmbeddings(dim=128), threshold=0.9)

    assert await cache.lookup("assistant", ["kb1"], "报销时限是几天") is None  # miss
    await cache.store("assistant", ["kb1"], "报销时限是几天", "30 天内提交 [1]", [{"id": 1}])

    hit = await cache.lookup("assistant", ["kb1"], "报销时限是几天")
    assert hit is not None and "30 天" in hit.answer and hit.similarity >= 0.9

    # 作用域隔离：不同 kb 不命中
    assert await cache.lookup("assistant", ["kb2"], "报销时限是几天") is None

    stats = await cache.stats()
    assert stats["entries"] == 1 and stats["session_hits"] == 1

    cleared = await cache.clear()
    assert cleared == 1 and await cache.lookup("assistant", ["kb1"], "报销时限是几天") is None
    await engine.dispose()


async def test_semantic_cache_disabled(tmp_path):
    sessions, engine = await _sessions(tmp_path)
    cache = SemanticCache(sessions, MockEmbeddings(dim=64), enabled=False)
    await cache.store("assistant", [], "q", "a", [])
    assert await cache.lookup("assistant", [], "q") is None
    await engine.dispose()


# ---------- RAG 进阶 ----------


async def test_rewrite_and_hyde_fallback_on_error():
    class BoomLLM(MockChatModel):
        async def complete(self, *a, **k):
            raise RuntimeError("down")

    llm = BoomLLM()
    # 失败时回退原查询，不抛异常
    assert await rewrite_query(llm, "原始问题") == "原始问题"
    assert await hyde_document(llm, "原始问题") == "原始问题"


async def test_hyde_generates_text():
    llm = MockChatModel(script=["这是一段关于报销制度的假设性文档，说明费用需在30天内提交。"])
    doc = await hyde_document(llm, "报销时限")
    assert "报销" in doc


async def test_compress_context_extracts_relevant():
    from agentforge.rag.retriever import RetrievedChunk

    llm = MockChatModel(
        script=[
            '{"relevant": true, "extract": "报销单必须在30天内提交。"}',
            '{"relevant": false, "extract": ""}',
        ]
    )
    chunks = [
        RetrievedChunk(
            chunk_id="c1", document_id="d", kb_id="k", content="报销单必须在30天内提交。另外还有很多无关内容。"
        ),
        RetrievedChunk(chunk_id="c2", document_id="d", kb_id="k", content="完全无关的内容"),
    ]
    out = await compress_context(llm, "报销时限", chunks)
    assert len(out) == 1 and out[0].content == "报销单必须在30天内提交。"


async def test_compress_context_rejects_generated_extract():
    from agentforge.rag.retriever import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="d",
        kb_id="k",
        content="原文只说明需要提交报销单。",
    )
    llm = MockChatModel(script=['{"relevant": true, "extract": "报销必须在30天内提交。"}'])
    out = await compress_context(llm, "报销时限", [chunk])
    assert out[0].content == chunk.content


async def test_rag_pipeline_parent_child(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/rag.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    emb = MockEmbeddings(dim=64)

    async with sessions() as db:
        user = User(username="u", password_hash="x")
        db.add(user)
        await db.flush()
        kb = KnowledgeBase(user_id=user.id, name="kb")
        db.add(kb)
        await db.flush()
        doc = Document(kb_id=kb.id, filename="制度.md", status="ready")
        db.add(doc)
        await db.flush()
        contents = ["第一段：适用范围说明。", "第二段：报销单必须在30天内提交。", "第三段：逾期需要审批。"]
        vecs = await emb.embed(contents)
        for i, (c, v) in enumerate(zip(contents, vecs, strict=True)):
            db.add(Chunk(document_id=doc.id, kb_id=kb.id, seq=i, content=c, terms=tokenize(c), embedding=v))
        await db.commit()
        kb_id = kb.id

    pipeline = RagPipeline(HybridRetriever(sessions, emb), MockChatModel())
    results, trace = await pipeline.retrieve(
        [kb_id], "报销时限", top_k=1, opts=RagOptions(parent_child=True, parent_window=1)
    )
    assert results and results[0].expanded
    # 父块扩展应包含相邻段落
    assert "第一段" in results[0].content and "第三段" in results[0].content
    assert "parent_child" in trace["steps"]
    await engine.dispose()


# ---------- 自定义 HTTP 工具 ----------


async def test_custom_tool_schema_and_ssrf():
    row = CustomTool(
        user_id="u",
        name="get_weather",
        description="查询天气",
        method="GET",
        url_template="http://127.0.0.1/api?city={city}",
        params_schema=[{"name": "city", "type": "string", "required": True, "location": "path"}],
    )
    tool = build_custom_tool(row)
    assert tool.name == "get_weather"
    assert tool.parameters["properties"]["city"]["type"] == "string"
    # 内网地址被 SSRF 防护拦截
    result = await tool.execute({"city": "北京"})
    assert not result.ok
