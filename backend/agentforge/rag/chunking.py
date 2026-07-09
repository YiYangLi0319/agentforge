"""语义分块：尊重标题/段落/句子边界，token 预算控制 + 相邻块重叠。"""

import re

from pydantic import BaseModel

from agentforge.core.messages import estimate_tokens
from agentforge.rag.parsers import Section

_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")


class ChunkDraft(BaseModel):
    content: str
    heading: str = ""
    seq: int = 0
    tokens: int = 0


def _split_long_text(text: str, budget: int) -> list[str]:
    """超长段落按句子切开，单句仍超长则硬切。"""
    pieces: list[str] = []
    for sent in _SENTENCE_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if estimate_tokens(sent) <= budget:
            pieces.append(sent)
        else:
            step = max(budget * 2, 100)  # 近似字符数
            for i in range(0, len(sent), step):
                pieces.append(sent[i : i + step])
    return pieces


def chunk_sections(
    sections: list[Section], *, chunk_tokens: int = 350, overlap_tokens: int = 60
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []

    for section in sections:
        paragraphs: list[str] = []
        for para in re.split(r"\n\s*\n", section.text):
            para = para.strip()
            if not para:
                continue
            if estimate_tokens(para) > chunk_tokens:
                paragraphs.extend(_split_long_text(para, chunk_tokens))
            else:
                paragraphs.append(para)

        current: list[str] = []
        current_tokens = 0
        for para in paragraphs:
            pt = estimate_tokens(para)
            if current and current_tokens + pt > chunk_tokens:
                content = "\n".join(current)
                chunks.append(ChunkDraft(content=content, heading=section.heading))
                # 重叠：保留尾部若干段落作为下一块开头
                overlap: list[str] = []
                acc = 0
                for p in reversed(current):
                    acc += estimate_tokens(p)
                    if acc > overlap_tokens:
                        break
                    overlap.insert(0, p)
                current = overlap
                current_tokens = sum(estimate_tokens(p) for p in current)
            current.append(para)
            current_tokens += pt
        if current:
            chunks.append(ChunkDraft(content="\n".join(current), heading=section.heading))

    for i, c in enumerate(chunks):
        c.seq = i
        c.tokens = estimate_tokens(c.content)
    return [c for c in chunks if c.content.strip()]
