"""文档解析：PDF / Word / Markdown / 纯文本 -> 结构化 Section 序列。"""

import io
from pathlib import Path

from pydantic import BaseModel


class Section(BaseModel):
    text: str
    heading: str = ""  # 标题路径，如 "部署指南 > 环境要求"


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt"}


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


def split_markdown_sections(text: str) -> list[Section]:
    """按标题层级切分 markdown，heading 记录完整标题路径。"""
    sections: list[Section] = []
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            path = " > ".join(h for _, h in heading_stack)
            sections.append(Section(text=content, heading=path))
        buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            if 1 <= level <= 6 and title:
                flush()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                continue
        buffer.append(line)
    flush()
    if not sections and text.strip():
        sections.append(Section(text=text.strip()))
    return sections


def _parse_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    sections = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(Section(text=text, heading=f"第 {i} 页"))
    return sections


def _parse_docx(data: bytes) -> list[Section]:
    import docx

    document = docx.Document(io.BytesIO(data))
    sections: list[Section] = []
    current_heading = ""
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            sections.append(Section(text=content, heading=current_heading))
        buffer.clear()

    for para in document.paragraphs:
        style = (para.style.name or "") if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith(("Heading", "标题")):
            flush()
            current_heading = text
        else:
            buffer.append(text)
    flush()
    return sections


def parse_document(filename: str, data: bytes) -> list[Section]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(data)
    if ext == ".docx":
        return _parse_docx(data)
    if ext in (".md", ".markdown", ".txt"):
        return split_markdown_sections(_decode(data))
    raise ValueError(f"不支持的文件类型: {ext}（支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}）")
