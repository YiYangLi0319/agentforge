"""RAG 检索管道：编排 查询改写/HyDE -> 混合检索 -> 父块扩展 -> 上下文压缩。

检索工具与检索 Playground 都走这里，保证行为一致、可配置。
"""

from dataclasses import dataclass

from agentforge.core.llm.base import ChatModel
from agentforge.rag.enhance import compress_context, hyde_document, rewrite_query
from agentforge.rag.retriever import HybridRetriever, RetrievedChunk


@dataclass
class RagOptions:
    mode: str = "hybrid"
    rerank: bool = True
    query_rewrite: bool = False
    hyde: bool = False
    compression: bool = False
    parent_child: bool = True
    parent_window: int = 1


class RagPipeline:
    def __init__(self, retriever: HybridRetriever, llm: ChatModel):
        self.retriever = retriever
        self.llm = llm

    async def retrieve(
        self, kb_ids: list[str], query: str, top_k: int, opts: RagOptions
    ) -> tuple[list[RetrievedChunk], dict]:
        """返回 (结果, 本次生效的增强步骤 trace)。"""
        trace: dict = {"original_query": query, "steps": []}

        search_query = query
        if opts.query_rewrite:
            search_query = await rewrite_query(self.llm, query)
            trace["rewritten_query"] = search_query
            trace["steps"].append("query_rewrite")

        vector_query = None
        if opts.hyde:
            vector_query = await hyde_document(self.llm, search_query)
            trace["hyde_doc"] = vector_query[:200]
            trace["steps"].append("hyde")

        results = await self.retriever.search(
            kb_ids,
            search_query,
            top_k=top_k,
            mode=opts.mode,
            rerank=opts.rerank,
            vector_query=vector_query,
            parent_window=opts.parent_window if opts.parent_child else 0,
        )
        if opts.parent_child:
            trace["steps"].append("parent_child")

        if opts.compression and results:
            results = await compress_context(self.llm, query, results)
            trace["steps"].append("compression")

        return results, trace
