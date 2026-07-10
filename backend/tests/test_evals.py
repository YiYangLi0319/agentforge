"""评估框架测试：指标计算正确性 + LLM-as-judge 结构化评审。"""

import pytest

from agentforge.core.llm.mock import MockChatModel
from agentforge.evals.judge import judge_answer, judge_task
from agentforge.evals.metrics import aggregate, hit_rate_at_k, mrr, ndcg_at_k, recall_at_k
from agentforge.evals.runner import threshold_failures


def test_retrieval_metrics():
    retrieved = ["a", "b", "c", "d"]
    relevant = {"b", "e"}
    assert recall_at_k(retrieved, relevant, 4) == 0.5
    assert hit_rate_at_k(retrieved, relevant, 1) == 0.0
    assert hit_rate_at_k(retrieved, relevant, 2) == 1.0
    assert mrr(retrieved, relevant) == 0.5
    assert mrr(["x"], relevant) == 0.0

    # 完美排名 nDCG=1
    assert ndcg_at_k(["b", "e", "a"], {"b", "e"}, 3) == pytest.approx(1.0)
    assert 0 < ndcg_at_k(["a", "b", "e"], {"b", "e"}, 3) < 1.0
    assert aggregate([1.0, 0.0]) == 0.5
    assert aggregate([]) == 0.0


def test_eval_threshold_gate():
    result = {"metrics": {"recall@5": 0.9, "mrr": 0.6}}
    assert threshold_failures(result, ["recall@5=0.8"]) == []
    assert threshold_failures(result, ["mrr=0.7"]) == ["mrr=0.6 低于阈值 0.7"]
    assert "不存在" in threshold_failures(result, ["citation=0.8"])[0]


async def test_judge_answer_structured():
    judge = MockChatModel(
        script=['{"faithfulness": 5, "relevance": 4, "citation": 5, "reason": "引用规范且忠实"}']
    )
    result, usage = await judge_answer(
        judge, question="报销时限？", answer="30 天内提交 [1]。", context="[1] 报销制度……"
    )
    assert result.faithfulness == 5 and result.citation == 5
    assert usage.total_tokens > 0
    # 评审提示词包含问题与答案
    sent = judge.calls[0]["messages"][-1]["content"]
    assert "报销时限" in sent and "30 天" in sent


async def test_judge_task_offline_mock():
    judge = MockChatModel()  # 自动模式：按 schema 生成合法数据
    result, _ = await judge_task(judge, task="计算 1+1", final_answer="2")
    assert isinstance(result.success, bool) and 1 <= result.quality <= 5
