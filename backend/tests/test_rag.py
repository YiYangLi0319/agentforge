"""RAG 管道测试：解析、分块、BM25、RRF、混合检索（SQLite 路径）、引用溯源。"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentforge.core.llm.embeddings import MockEmbeddings
from agentforge.db.base import Base, build_engine
from agentforge.db.models import Chunk, Document, KnowledgeBase, User
from agentforge.rag.bm25 import BM25Index, rrf_fuse
from agentforge.rag.chunking import chunk_sections
from agentforge.rag.citations import (
    audit_citations,
    cited_sources,
    extract_cited_ids,
    format_context_with_citations,
    sanitize_invalid_citations,
)
from agentforge.rag.parsers import Section, parse_document, split_markdown_sections
from agentforge.rag.retriever import HybridRetriever
from agentforge.rag.tokenize import tokenize


def test_markdown_section_split_keeps_heading_path():
    md = "# 手册\n\n简介内容\n\n## 部署\n\n### 环境\n\n需要 PostgreSQL。\n\n## 计费\n\n按年计费。"
    sections = split_markdown_sections(md)
    headings = [s.heading for s in sections]
    assert "手册" in headings[0]
    assert any(h == "手册 > 部署 > 环境" for h in headings)
    assert any("计费" in h for h in headings)


def test_parse_document_txt_and_unsupported():
    sections = parse_document("note.txt", "第一段。\n\n第二段。".encode())
    assert len(sections) >= 1
    with pytest.raises(ValueError, match="不支持"):
        parse_document("evil.exe", b"xx")


def test_chunking_respects_budget_and_overlap():
    long_para = "这是一个很长的句子，用来测试分块。" * 5
    sections = [Section(text="\n\n".join([long_para] * 8), heading="测试 > 分块")]
    chunks = chunk_sections(sections, chunk_tokens=120, overlap_tokens=30)
    assert len(chunks) >= 2
    assert all(c.tokens <= 200 for c in chunks)
    assert all(c.heading == "测试 > 分块" for c in chunks)
    # 相邻块有重叠内容
    assert chunks[1].content.split("\n")[0] in chunks[0].content


def test_tokenize_chinese_and_stopwords():
    tokens = tokenize("请问报销的时限是多少天？What is the deadline?")
    assert "报销" in tokens and "时限" in tokens and "deadline" in tokens
    assert "的" not in tokens and "the" not in tokens and "？" not in tokens


def test_bm25_ranks_relevant_doc_first():
    docs = [
        ("d1", tokenize("报销单必须在费用发生后三十天内提交，逾期需要特批")),
        ("d2", tokenize("年假天数与入职年限相关，满一年五天")),
        ("d3", tokenize("生产环境访问必须通过堡垒机，禁止直连")),
    ]
    index = BM25Index(docs)
    hits = index.search(tokenize("报销提交时限"), top_k=3)
    assert hits[0][0] == "d1" and hits[0][1] > 0


def test_rrf_fusion_prefers_docs_in_both_channels():
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
    assert fused["a"] > fused["c"] and fused["b"] > fused["c"]
    top = max(fused, key=fused.get)
    assert top in ("a", "b")


async def _seed_kb(sessions) -> str:
    emb = MockEmbeddings(dim=64)
    async with sessions() as session:
        user = User(username="u", password_hash="x")
        session.add(user)
        await session.flush()
        kb = KnowledgeBase(user_id=user.id, name="制度库")
        session.add(kb)
        await session.flush()
        doc = Document(kb_id=kb.id, filename="考勤制度.md", status="ready")
        session.add(doc)
        await session.flush()
        contents = [
            "报销单必须在费用发生后 30 天内提交，逾期需 VP 特批。",
            "年假：入职满 1 年 5 天，满 3 年 10 天，满 5 年 15 天。",
            "一线城市住宿标准为每人每晚 600 元上限。",
        ]
        vectors = await emb.embed(contents)
        for i, (content, vec) in enumerate(zip(contents, vectors, strict=True)):
            session.add(
                Chunk(
                    document_id=doc.id,
                    kb_id=kb.id,
                    seq=i,
                    content=content,
                    heading="考勤制度",
                    terms=tokenize(content),
                    embedding=vec,
                )
            )
        await session.commit()
        return kb.id


async def test_hybrid_retriever_sqlite_end_to_end(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/rag.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    kb_id = await _seed_kb(sessions)

    retriever = HybridRetriever(sessions, MockEmbeddings(dim=64))
    results = await retriever.search([kb_id], "报销单提交时限是多久", top_k=2)
    assert results and "30 天" in results[0].content
    assert results[0].bm25_score > 0 and results[0].rrf_score > 0
    assert results[0].filename == "考勤制度.md"

    # 三种模式都能工作
    for mode in ("vector", "keyword", "hybrid"):
        r = await retriever.search([kb_id], "年假有几天", top_k=2, mode=mode)
        assert r, f"mode={mode} 应有结果"

    # BM25 缓存命中（相同语料不重建）
    key = ",".join(sorted([kb_id]))
    cached = retriever._bm25_cache[key]
    await retriever.search([kb_id], "住宿标准", top_k=1)
    assert retriever._bm25_cache[key][1] is cached[1]
    await engine.dispose()


def test_citation_registry_and_extraction():
    from agentforge.rag.retriever import RetrievedChunk

    state: dict = {}
    chunks = [
        RetrievedChunk(chunk_id="c1", document_id="d1", kb_id="k", filename="a.md", content="内容一"),
        RetrievedChunk(chunk_id="c2", document_id="d1", kb_id="k", filename="a.md", content="内容二"),
    ]
    context = format_context_with_citations(chunks, state)
    assert context.startswith("[1]") and "[2]" in context
    # 同一 chunk 再次检索复用编号
    format_context_with_citations(chunks[:1], state)
    assert len(state["sources"]) == 2

    answer = "根据制度规定 [1]，报销需在 30 天内提交。另见 [3]（不存在）。"
    assert extract_cited_ids(answer) == {1, 3}
    used = cited_sources(answer, state)
    assert len(used) == 1 and used[0]["id"] == 1
    audit = audit_citations(answer, state["sources"], require_citations=True)
    assert audit.invalid_ids == [3] and not audit.passed
    cleaned = sanitize_invalid_citations(answer, state["sources"])
    assert "[无效来源:3]" in cleaned and "[3]" not in cleaned


def test_audit_requires_sources_when_citations_required():
    # 无任何来源时，要求引用的报告不能判为通过（否则会被误标 succeeded 并可公开分享）
    audit = audit_citations("这是一段没有任何来源支撑的结论。", [], require_citations=True)
    assert not audit.passed and audit.issues
    # 不要求引用时，纯说明性文本可以通过
    relaxed = audit_citations("这是一段自由回答。", [], require_citations=False)
    assert relaxed.passed
