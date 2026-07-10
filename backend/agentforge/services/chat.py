"""对话服务：装配助手/团队 Agent，串联记忆、检索、引用与消息持久化。"""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select, update

from agentforge.api.app import Container
from agentforge.core.agent import Agent
from agentforge.core.events import (
    AgentEvent,
    AssistantMessage,
    CacheHit,
    GuardrailTriggered,
    MemoryUpdated,
    RunFinished,
    SourcesUpdated,
)
from agentforge.core.memory import ConversationMemory, LongTermMemory, render_memories
from agentforge.core.messages import Message, Role, Usage
from agentforge.core.runtime import RunContext
from agentforge.core.supervisor import WorkerSpec, build_supervisor
from agentforge.core.tools.base import Tool, ToolRegistry
from agentforge.core.tools.builtins import calculator, current_time
from agentforge.core.tools.python_sandbox import python_execute
from agentforge.core.tools.retrieval import search_knowledge_base
from agentforge.core.tools.web_fetch import web_fetch
from agentforge.core.tools.web_search import web_search
from agentforge.db.models import ChatMessage, ChatSession
from agentforge.rag.citations import cited_sources
from agentforge.rag.pipeline import RagPipeline
from agentforge.services.custom_tools import load_custom_tools

logger = logging.getLogger(__name__)


def _assistant_system_prompt(has_kb: bool, memories_note: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    parts = [
        "你是 AgentForge 企业智能助手，专业、简洁、可靠。今天是 " + today + "。",
        "回答规则：",
        "- 需要实时信息或外部事实时使用 web_search / web_fetch；",
        "- 需要精确算术用 calculator，需要复杂计算/数据处理用 python_execute，需要当前时间用 current_time；",
    ]
    if has_kb:
        parts.append(
            "- 涉及企业内部知识（制度/产品/规范等）必须先调用 search_knowledge_base 检索，"
            "并在引用检索内容的句末标注来源编号 [n]；检索不到时如实说明。"
        )
    parts.append("- 不确定的信息不要编造；工具失败时说明原因并给出替代方案。")
    if memories_note:
        parts.append("\n" + memories_note)
    return "\n".join(parts)


def _sandbox_tool(container: Container) -> Tool:
    """按配置决定 python 沙箱是否需要审批（演示 HITL 的开关）。"""
    t = python_execute
    t.requires_approval = container.settings.sandbox_requires_approval
    t.timeout = float(container.settings.sandbox_timeout) + 10
    return t


def _build_assistant(
    container: Container, kb_ids: list[str], memories_note: str, extra_tools: list[Tool] | None = None
) -> Agent:
    tools: list[Tool] = [web_search, web_fetch, calculator, current_time, _sandbox_tool(container)]
    if kb_ids:
        tools.insert(0, search_knowledge_base)
    tools.extend(extra_tools or [])
    return Agent(
        name="assistant",
        llm=container.llm,
        tools=ToolRegistry(tools),
        system_prompt=_assistant_system_prompt(bool(kb_ids), memories_note),
        max_steps=container.settings.agent_max_steps,
        token_budget=container.settings.agent_token_budget,
        temperature=container.settings.llm_temperature,
    )


_TOOL_MAP: dict[str, Tool] = {
    "web_search": web_search,
    "web_fetch": web_fetch,
    "calculator": calculator,
    "current_time": current_time,
    "search_knowledge_base": search_knowledge_base,
}


async def _build_custom(
    container: Container, custom_agent_id: str, memories_note: str, extra_tools: list[Tool] | None = None
) -> Agent | None:
    """按用户自定义 Agent 配置构建；配置不存在则返回 None（回退到默认助手）。"""
    from sqlalchemy import select as _select

    from agentforge.db.models import CustomAgent

    async with container.sessions() as db:
        cfg = (
            await db.execute(_select(CustomAgent).where(CustomAgent.id == custom_agent_id))
        ).scalar_one_or_none()
    if cfg is None:
        return None

    tools: list[Tool] = []
    for name in cfg.tools or []:
        if name == "python_execute":
            tools.append(_sandbox_tool(container))
        elif name in _TOOL_MAP:
            tools.append(_TOOL_MAP[name])
    tools.extend(extra_tools or [])

    prompt = cfg.system_prompt or "你是一个乐于助人的 AI 助手。"
    if cfg.kb_ids and search_knowledge_base not in tools:
        tools.insert(0, search_knowledge_base)
        prompt += "\n涉及知识库内容必须先调用 search_knowledge_base 检索，并在引用处标注来源编号 [n]。"
    if memories_note:
        prompt += "\n\n" + memories_note

    return Agent(
        name=cfg.name or "custom",
        llm=container.llm,
        tools=ToolRegistry(tools),
        system_prompt=prompt,
        max_steps=cfg.max_steps,
        token_budget=container.settings.agent_token_budget,
        temperature=cfg.temperature,
    )


def _build_team(
    container: Container, kb_ids: list[str], memories_note: str, extra_tools: list[Tool] | None = None
) -> Agent:
    """团队模式：Supervisor 委派检索/调研/计算三类专家（多 Agent 演示）。"""
    coder_tools: list[Tool] = [_sandbox_tool(container), calculator, *(extra_tools or [])]
    workers = [
        WorkerSpec(
            name="web_researcher",
            description="联网调研专家：搜索互联网并阅读网页，产出带引用的调研纪要",
            build=lambda: Agent(
                name="web_researcher",
                llm=container.llm,
                tools=ToolRegistry([web_search, web_fetch]),
                system_prompt="你是联网调研专家，先搜索后阅读，输出要点式纪要并保留来源编号 [n]。",
                max_steps=4,
                stream_final=False,
            ),
        ),
        WorkerSpec(
            name="coder",
            description="计算与数据处理专家：编写并执行 Python 代码/计算器完成计算、统计与验证",
            build=lambda: Agent(
                name="coder",
                llm=container.llm,
                tools=ToolRegistry(coder_tools),
                system_prompt="你是计算专家，用 calculator 或 python_execute 完成计算并核对结果，输出结论。",
                max_steps=3,
                stream_final=False,
            ),
        ),
    ]
    if kb_ids:
        workers.insert(
            0,
            WorkerSpec(
                name="kb_expert",
                description="企业知识库专家：检索内部制度/产品/规范文档并给出带引用 [n] 的回答",
                build=lambda: Agent(
                    name="kb_expert",
                    llm=container.llm,
                    tools=ToolRegistry([search_knowledge_base]),
                    system_prompt="你是企业知识库专家，先检索再回答，引用句末标注 [n]。",
                    max_steps=3,
                    stream_final=False,
                ),
            ),
        )
    sup = build_supervisor(
        llm=container.llm,
        workers=workers,
        max_steps=container.settings.agent_max_steps,
        token_budget=container.settings.agent_token_budget,
    )
    if memories_note:
        sup.system_prompt += "\n\n" + memories_note
    return sup


async def load_history(container: Container, session_id: str) -> list[Message]:
    async with container.sessions() as db:
        rows = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [Message(role=Role(r.role), content=r.content) for r in rows]


async def _persist_assistant(
    container: Container,
    chat_session: ChatSession,
    content: str,
    sources: list[dict],
    run_id: str,
    summary: str,
) -> None:
    """持久化助手消息（+可选更新会话摘要），供护栏拒答 / 缓存命中等快速路径复用。"""
    async with container.sessions() as db:
        if content:
            db.add(
                ChatMessage(
                    session_id=chat_session.id,
                    role="assistant",
                    content=content,
                    sources=sources,
                    run_id=run_id,
                )
            )
        values: dict = {"updated_at": datetime.now(UTC)}
        if summary:
            values["summary"] = summary
        await db.execute(update(ChatSession).where(ChatSession.id == chat_session.id).values(**values))
        await db.commit()


def make_chat_factory(container: Container, chat_session: ChatSession, user_message: str):
    """返回 RunManager 可驱动的事件流工厂：记忆准备 -> Agent 执行 -> 落库与记忆更新。"""

    async def factory(ctx: RunContext) -> AsyncIterator[AgentEvent]:
        ctx.kb_ids = list(chat_session.kb_ids or [])
        ctx.services.update(
            {
                "retriever": container.retriever,
                "rag_pipeline": RagPipeline(container.retriever, container.llm),
                "search": container.search,
                "settings": container.settings,
            }
        )

        # 自定义 Agent：加载配置，用其绑定的知识库；缓存作用域也据此隔离
        custom_cfg = None
        if chat_session.custom_agent_id:
            from sqlalchemy import select as _select

            from agentforge.db.models import CustomAgent

            async with container.sessions() as db:
                custom_cfg = (
                    await db.execute(
                        _select(CustomAgent).where(CustomAgent.id == chat_session.custom_agent_id)
                    )
                ).scalar_one_or_none()
            if custom_cfg is not None:
                ctx.kb_ids = list(custom_cfg.kb_ids or [])
        cache_scope = chat_session.custom_agent_id or chat_session.agent_type

        # 0) 输入护栏：注入检测 + 内容审核，命中即拒绝，不进入 Agent
        guard_in = container.guardrails.check_input(user_message)
        if guard_in.blocked:
            refusal = container.guardrails.refusal_message(guard_in)
            yield GuardrailTriggered(
                stage="input", verdict="block", categories=guard_in.categories,
                detail="；".join(guard_in.reasons)[:200],
            )
            yield AssistantMessage(content=refusal, final=True)
            await _persist_assistant(container, chat_session, refusal, [], ctx.run_id, "")
            yield RunFinished(output={"text": refusal, "sources": [], "blocked": True})
            return

        # 1) 语义缓存：相似问题直接复用历史答案
        cached = await container.semantic_cache.lookup(cache_scope, ctx.kb_ids, user_message)
        if cached is not None:
            yield CacheHit(similarity=cached.similarity)
            answer = cached.answer
            if container.settings.guardrails_mask_pii:
                answer = container.guardrails.check_output(answer).text
            yield AssistantMessage(content=answer, final=True)
            yield SourcesUpdated(sources=cached.sources)
            await _persist_assistant(container, chat_session, answer, cached.sources, ctx.run_id, "")
            yield RunFinished(
                output={"text": answer, "sources": cached.sources, "cached": True},
                usage=Usage(),
            )
            return

        # 2) 记忆准备：长期记忆召回 + 历史压缩
        ltm = LongTermMemory(container.memory_store, container.embeddings, container.llm)
        memories: list[str] = []
        try:
            memories = await ltm.retrieve(ctx.user_id or "", user_message, k=4)
        except Exception as e:  # noqa: BLE001 记忆失败不阻断对话
            logger.warning("长期记忆召回失败: %s", e)

        history = await load_history(container, chat_session.id)
        conv_memory = ConversationMemory(
            container.llm, token_budget=container.settings.chat_history_token_budget
        )
        prepared, new_summary = await conv_memory.prepare(history, chat_session.summary or "")

        # 3) 装配工具（内置 + 自定义 HTTP + MCP）并执行
        extra_tools: list[Tool] = list(container.mcp.tools)
        try:
            extra_tools += await load_custom_tools(container.sessions, ctx.user_id or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("加载自定义工具失败: %s", e)

        memories_note = render_memories(memories)
        agent: Agent | None = None
        if custom_cfg is not None:
            agent = await _build_custom(
                container, chat_session.custom_agent_id or "", memories_note, extra_tools
            )
        if agent is None:
            builder = _build_team if chat_session.agent_type == "team" else _build_assistant
            agent = builder(container, ctx.kb_ids, memories_note, extra_tools)

        final_text = ""
        async for ev in agent.run(prepared, ctx):
            if isinstance(ev, RunFinished):
                final_text = str(ev.output.get("text", ""))
                # 输出护栏：PII 脱敏
                guard_out = container.guardrails.check_output(final_text)
                if guard_out.pii_types:
                    final_text = guard_out.text
                    yield GuardrailTriggered(
                        stage="output", verdict="allow", categories=["pii:" + ",".join(guard_out.pii_types)],
                        detail="已对输出中的敏感信息脱敏",
                    )
                sources = cited_sources(final_text, ctx.state)
                ev.output["text"] = final_text
                ev.output["sources"] = sources
                yield SourcesUpdated(sources=sources)
                yield ev
            else:
                yield ev

        # 4) 写入语义缓存（仅缓存成功的实质回答）
        if final_text.strip():
            try:
                await container.semantic_cache.store(
                    cache_scope,
                    ctx.kb_ids,
                    user_message,
                    final_text,
                    cited_sources(final_text, ctx.state),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("写入语义缓存失败: %s", e)

        # 5) 持久化助手消息与会话摘要
        sources = cited_sources(final_text, ctx.state)
        async with container.sessions() as db:
            if final_text:
                db.add(
                    ChatMessage(
                        session_id=chat_session.id,
                        role="assistant",
                        content=final_text,
                        sources=sources,
                        run_id=ctx.run_id,
                    )
                )
            await db.execute(
                update(ChatSession)
                .where(ChatSession.id == chat_session.id)
                .values(summary=new_summary, updated_at=datetime.now(UTC))
            )
            await db.commit()

        # 4) 长期记忆抽取（失败不影响本轮结果）
        try:
            added = await ltm.extract_and_store(
                ctx.user_id or "",
                [Message.user(user_message), Message.assistant(final_text)],
            )
            if added:
                yield MemoryUpdated(added=added)
        except Exception as e:  # noqa: BLE001
            logger.warning("长期记忆抽取失败: %s", e)

    return factory


def make_resume_factory(container: Container, run_row):
    """从 checkpoint 恢复中断的 chat run：未完成的工具调用补占位结果后继续。"""

    async def factory(ctx: RunContext) -> AsyncIterator[AgentEvent]:
        ctx.services.update(
            {
                "retriever": container.retriever,
                "rag_pipeline": RagPipeline(container.retriever, container.llm),
                "search": container.search,
                "settings": container.settings,
            }
        )
        data = (run_row.checkpoint or {}).get("messages", [])
        messages = [Message.model_validate(m) for m in data]
        if messages and messages[-1].role == Role.ASSISTANT and messages[-1].tool_calls:
            for tc in messages[-1].tool_calls:
                messages.append(
                    Message.tool_result(tc.id, tc.name, "[系统] 服务已重启，该工具调用未执行，请重新处理。")
                )
        async with container.sessions() as db:
            chat_session = (
                await db.execute(select(ChatSession).where(ChatSession.id == run_row.session_id))
            ).scalar_one()
        ctx.kb_ids = list(chat_session.kb_ids or [])
        agent = _build_assistant(container, ctx.kb_ids, "")
        async for ev in agent.run(messages, ctx):
            yield ev

    return factory
