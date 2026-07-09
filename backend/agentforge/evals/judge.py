"""LLM-as-judge：忠实度 / 相关性 / 引用规范 三维度评审（结构化输出，1-5 分制）。"""

from pydantic import BaseModel, Field

from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message, Usage


class AnswerJudgement(BaseModel):
    faithfulness: int = Field(ge=1, le=5, description="忠实度：答案是否完全基于给定上下文，无编造")
    relevance: int = Field(ge=1, le=5, description="相关性：答案是否直接回应了问题")
    citation: int = Field(ge=1, le=5, description="引用规范：事实句是否标注 [n] 且编号真实存在")
    reason: str = Field(default="", description="一句话评审理由")


async def judge_answer(
    judge_llm: ChatModel,
    *,
    question: str,
    answer: str,
    context: str = "",
    reference: str = "",
) -> tuple[AnswerJudgement, Usage]:
    prompt = (
        "你是严格的答案质量评审员。请按三个维度对答案打分（1-5 分）：\n"
        "1. faithfulness 忠实度：答案的每个事实是否都能在上下文中找到依据（无上下文时基于常识判断）；\n"
        "2. relevance 相关性：是否直接、完整地回答了问题；\n"
        "3. citation 引用规范：引用事实处是否标注了 [n] 编号。\n\n"
        f"【问题】\n{question}\n\n"
    )
    if context:
        prompt += f"【检索上下文】\n{context[:4000]}\n\n"
    if reference:
        prompt += f"【参考答案】\n{reference}\n\n"
    prompt += f"【待评审答案】\n{answer[:4000]}"
    return await complete_json(judge_llm, [Message.user(prompt)], AnswerJudgement)


class TaskJudgement(BaseModel):
    success: bool = Field(description="Agent 是否完成了任务目标")
    quality: int = Field(ge=1, le=5, description="完成质量 1-5")
    reason: str = Field(default="", description="一句话评审理由")


async def judge_task(
    judge_llm: ChatModel, *, task: str, final_answer: str
) -> tuple[TaskJudgement, Usage]:
    prompt = (
        "你是 Agent 任务完成度评审员。判断 Agent 的最终输出是否完成了任务目标并打分。\n\n"
        f"【任务】\n{task}\n\n【Agent 最终输出】\n{final_answer[:4000]}"
    )
    return await complete_json(judge_llm, [Message.user(prompt)], TaskJudgement)
