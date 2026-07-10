"""引用溯源：检索结果 -> 带编号上下文；回答 -> 提取实际引用的来源子集。"""

import re

from pydantic import BaseModel, Field

from agentforge.core.tools.sources import register_source
from agentforge.rag.retriever import RetrievedChunk

_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])|\n+")
_FACT_MARKERS = (
    "%",
    "根据",
    "显示",
    "达到",
    "增长",
    "下降",
    "发布",
    "要求",
    "必须",
    "截至",
    "数据",
    "研究",
    "报告",
)


class CitationAudit(BaseModel):
    """无需额外 LLM 的确定性引用完整性审计。"""

    cited_ids: list[int] = Field(default_factory=list)
    invalid_ids: list[int] = Field(default_factory=list)
    factual_claims: int = 0
    cited_claims: int = 0
    coverage: float = 0.0
    verified_ratio: float = 0.0
    passed: bool = False
    issues: list[str] = Field(default_factory=list)


def format_context_with_citations(chunks: list[RetrievedChunk], state: dict) -> str:
    """把检索结果格式化为带全局编号的上下文块，同时登记到来源注册表。"""
    if not chunks:
        return "（知识库中没有找到相关内容）"
    blocks = []
    for c in chunks:
        n = register_source(
            state,
            origin="kb",
            title=c.heading or c.filename,
            snippet=c.content[:200],
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            filename=c.filename,
            heading=c.heading,
            verified=True,
            evidence=c.content,
        )
        header = f"[{n}] 《{c.filename}》" + (f" - {c.heading}" if c.heading else "")
        blocks.append(f"{header}\n{c.content}")
    return "\n\n".join(blocks)


def extract_cited_ids(answer: str) -> set[int]:
    return {int(m) for m in _CITE_RE.findall(answer)}


def public_source(source: dict) -> dict:
    """移除仅供服务端核验的长证据正文，控制 API/缓存 payload 大小。"""
    return {k: v for k, v in source.items() if k != "evidence"}


def cited_sources(answer: str, state: dict) -> list[dict]:
    """返回答案中实际引用的来源（保持编号），无引用时返回空列表。"""
    sources: list[dict] = state.get("sources", [])
    used = extract_cited_ids(answer)
    return [public_source(s) for s in sources if s["id"] in used]


def sanitize_invalid_citations(answer: str, sources: list[dict]) -> str:
    """未知编号保留为普通文本提示而非可点击的可信引用。"""
    valid_ids = {int(s["id"]) for s in sources}
    return _CITE_RE.sub(
        lambda match: match.group(0) if int(match.group(1)) in valid_ids else f"[无效来源:{match.group(1)}]",
        answer,
    )


def audit_citations(
    answer: str,
    sources: list[dict],
    *,
    require_citations: bool = True,
    min_coverage: float = 0.6,
) -> CitationAudit:
    """检查编号存在性、事实句引用覆盖率和已抓取证据占比。

    这是确定性完整性门，不把词面重叠误称为事实蕴含；更深的 groundedness 仍交给
    看过证据正文的 judge。
    """
    cited_ids = sorted(extract_cited_ids(answer))
    by_id = {int(s["id"]): s for s in sources}
    invalid_ids = [source_id for source_id in cited_ids if source_id not in by_id]

    factual_claims = 0
    cited_claims = 0
    body = answer.split("\n## 参考来源", 1)[0]
    for raw in _SENTENCE_RE.split(body):
        sentence = raw.strip().lstrip("-*0123456789.、) ")
        if len(sentence) < 12 or sentence.startswith("#"):
            continue
        without_cites = _CITE_RE.sub("", sentence)
        looks_factual = any(ch.isdigit() for ch in without_cites) or any(
            marker in without_cites for marker in _FACT_MARKERS
        )
        if not looks_factual:
            continue
        factual_claims += 1
        if any(source_id in by_id for source_id in extract_cited_ids(sentence)):
            cited_claims += 1

    coverage = cited_claims / factual_claims if factual_claims else (1.0 if cited_ids else 0.0)
    valid_cited = [source_id for source_id in cited_ids if source_id in by_id]
    verified = sum(bool(by_id[source_id].get("verified")) for source_id in valid_cited)
    verified_ratio = verified / len(valid_cited) if valid_cited else 0.0

    issues: list[str] = []
    if invalid_ids:
        issues.append("存在无效来源编号：" + ", ".join(f"[{n}]" for n in invalid_ids))
    if require_citations and not sources:
        issues.append("没有可用来源，无法据实撰写")
    elif require_citations and not valid_cited:
        issues.append("报告没有引用任何可用来源")
    if factual_claims and coverage < min_coverage:
        issues.append(f"事实句引用覆盖率仅 {coverage:.0%}，低于 {min_coverage:.0%}")
    if valid_cited and verified_ratio == 0:
        issues.append("引用均来自搜索摘要，尚未抓取原文核验")

    # 要求引用时，必须存在来源且至少命中一个有效引用；无来源不能视为通过。
    passed = not invalid_ids and (not require_citations or bool(valid_cited))
    if factual_claims:
        passed = passed and coverage >= min_coverage
    return CitationAudit(
        cited_ids=cited_ids,
        invalid_ids=invalid_ids,
        factual_claims=factual_claims,
        cited_claims=cited_claims,
        coverage=round(coverage, 4),
        verified_ratio=round(verified_ratio, 4),
        passed=passed,
        issues=issues,
    )
