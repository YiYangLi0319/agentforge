"""知识库检索工具：Agent 主动决定何时检索、检索什么（Agentic RAG）。"""

from agentforge.core.tools.base import ToolContext, ToolResult, tool


@tool(name="search_knowledge_base", timeout=30.0, tags=["rag"])
async def search_knowledge_base(query: str, top_k: int = 5, ctx: ToolContext | None = None) -> ToolResult:
    """在企业知识库中检索与问题相关的内容。回答基于知识库的问题前必须先调用本工具；
    引用检索内容时必须在句末标注对应的来源编号，如 [1][2]。

    Args:
        query: 检索查询（用完整的自然语言问题，包含关键实体词）
        top_k: 返回的相关片段数量，默认 5
    """
    assert ctx is not None
    pipeline = ctx.services.get("rag_pipeline")
    if pipeline is None:
        return ToolResult.error("检索服务未初始化")
    if not ctx.kb_ids:
        return ToolResult.error("当前会话未绑定知识库，请先在会话设置中选择知识库")

    from agentforge.rag.citations import format_context_with_citations
    from agentforge.rag.pipeline import RagOptions

    settings = ctx.services.get("settings")
    opts = RagOptions(
        query_rewrite=bool(getattr(settings, "rag_query_rewrite", False)),
        hyde=bool(getattr(settings, "rag_hyde", False)),
        compression=bool(getattr(settings, "rag_compression", False)),
        parent_child=bool(getattr(settings, "rag_parent_child", True)),
    )
    results, trace = await pipeline.retrieve(ctx.kb_ids, query, min(max(top_k, 1), 10), opts)
    if ctx.run_ctx is not None:
        async with ctx.run_ctx.tracer.span("retrieval", "retrieval") as span:
            span.input = {"query": query, "kb_ids": ctx.kb_ids, "top_k": top_k, "enhance": trace.get("steps", [])}
            span.set_output(
                hits=[
                    {"chunk_id": r.chunk_id, "file": r.filename, "score": r.final_score}
                    for r in results
                ]
            )
    context = format_context_with_citations(results, ctx.state)
    return ToolResult(content=context, data={"count": len(results), "enhance": trace.get("steps", [])})
