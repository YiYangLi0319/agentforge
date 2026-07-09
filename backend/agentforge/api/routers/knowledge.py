"""知识库路由：知识库/文档管理、上传入库、检索 Playground、样例数据一键导入。"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.db.models import Chunk, Document, KnowledgeBase, User
from agentforge.rag.parsers import SUPPORTED_EXTENSIONS
from agentforge.services.ingestion import ingest_document

router = APIRouter()

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples" / "kb"


class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)


async def _own_kb(db: AsyncSession, user: User, kb_id: str) -> KnowledgeBase:
    kb = (
        await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user.id)
        )
    ).scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


@router.post("", status_code=201)
async def create_kb(
    body: KBCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    kb = KnowledgeBase(user_id=user.id, name=body.name, description=body.description)
    db.add(kb)
    await db.commit()
    return {"id": kb.id, "name": kb.name, "description": kb.description}


@router.get("")
async def list_kbs(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    kbs = (
        (
            await db.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.user_id == user.id)
                .order_by(desc(KnowledgeBase.updated_at))
            )
        )
        .scalars()
        .all()
    )
    result = []
    for kb in kbs:
        doc_count = (
            await db.execute(select(func.count(Document.id)).where(Document.kb_id == kb.id))
        ).scalar() or 0
        chunk_count = (
            await db.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb.id))
        ).scalar() or 0
        result.append(
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "doc_count": doc_count,
                "chunk_count": chunk_count,
                "updated_at": kb.updated_at.isoformat(),
            }
        )
    return result


@router.delete("/{kb_id}", status_code=204)
async def delete_kb(
    kb_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    kb = await _own_kb(db, user, kb_id)
    await db.execute(delete(Chunk).where(Chunk.kb_id == kb_id))
    await db.execute(delete(Document).where(Document.kb_id == kb_id))
    await db.delete(kb)
    await db.commit()


def _doc_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "filename": d.filename,
        "size": d.size,
        "status": d.status,
        "error": d.error,
        "chunk_count": d.chunk_count,
        "created_at": d.created_at.isoformat(),
    }


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    await _own_kb(db, user, kb_id)
    docs = (
        (
            await db.execute(
                select(Document).where(Document.kb_id == kb_id).order_by(desc(Document.created_at))
            )
        )
        .scalars()
        .all()
    )
    return [_doc_dict(d) for d in docs]


@router.post("/{kb_id}/documents", status_code=202)
async def upload_documents(
    kb_id: str,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> list[dict]:
    await _own_kb(db, user, kb_id)
    max_bytes = container.settings.max_upload_mb * 1024 * 1024
    accepted: list[dict] = []
    for f in files:
        filename = f.filename or "unnamed"
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {filename}")
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413, detail=f"文件过大: {filename}（上限 {container.settings.max_upload_mb}MB）"
            )
        doc = Document(kb_id=kb_id, filename=filename, mime=f.content_type or "", size=len(data))
        db.add(doc)
        await db.commit()
        # 原始文件留档（便于审计与重建索引）
        raw_path = Path(container.settings.upload_dir) / f"{doc.id}{ext}"
        raw_path.write_bytes(data)
        background.add_task(
            ingest_document, container.sessions, container.embeddings, doc.id, filename, data
        )
        accepted.append(_doc_dict(doc))
    return accepted


@router.post("/{kb_id}/load-samples", status_code=202)
async def load_samples(
    kb_id: str,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> list[dict]:
    """一键导入内置样例文档（星衡科技制度/产品手册），便于快速体验。"""
    await _own_kb(db, user, kb_id)
    if not SAMPLES_DIR.exists():
        raise HTTPException(status_code=404, detail="样例目录不存在")
    accepted = []
    for path in sorted(SAMPLES_DIR.glob("*.md")):
        data = path.read_bytes()
        doc = Document(kb_id=kb_id, filename=path.name, mime="text/markdown", size=len(data))
        db.add(doc)
        await db.commit()
        background.add_task(
            ingest_document, container.sessions, container.embeddings, doc.id, path.name, data
        )
        accepted.append(_doc_dict(doc))
    return accepted


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    kb_id: str,
    doc_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _own_kb(db, user, kb_id)
    doc = (
        await db.execute(select(Document).where(Document.id == doc_id, Document.kb_id == kb_id))
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    await db.execute(delete(Chunk).where(Chunk.document_id == doc_id))
    await db.delete(doc)
    await db.commit()


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|keyword)$")


@router.post("/{kb_id}/search")
async def search_kb(
    kb_id: str,
    body: SearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    """检索 Playground：返回混合检索的分数拆解，用于调试与演示。"""
    await _own_kb(db, user, kb_id)
    results = await container.retriever.search([kb_id], body.query, top_k=body.top_k, mode=body.mode)
    return {
        "query": body.query,
        "mode": body.mode,
        "results": [r.model_dump() for r in results],
    }
