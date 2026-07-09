"""会话与消息路由：会话 CRUD、发消息触发 Agent Run。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db, rate_limited
from agentforge.core.runtime import RunContext
from agentforge.db.models import ChatMessage, ChatSession, KnowledgeBase, User
from agentforge.services.chat import make_chat_factory

router = APIRouter()


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", max_length=256)
    agent_type: str = Field(default="assistant", pattern="^(assistant|team)$")
    kb_ids: list[str] = Field(default_factory=list)


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    agent_type: str | None = Field(default=None, pattern="^(assistant|team)$")
    kb_ids: list[str] | None = None


async def _own_session(db: AsyncSession, user: User, session_id: str) -> ChatSession:
    row = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def _session_dict(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "agent_type": s.agent_type,
        "kb_ids": s.kb_ids or [],
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if body.kb_ids:
        owned = (
            (
                await db.execute(
                    select(KnowledgeBase.id).where(
                        KnowledgeBase.id.in_(body.kb_ids), KnowledgeBase.user_id == user.id
                    )
                )
            )
            .scalars()
            .all()
        )
        if set(owned) != set(body.kb_ids):
            raise HTTPException(status_code=400, detail="包含不存在或无权访问的知识库")
    session = ChatSession(
        user_id=user.id, title=body.title, agent_type=body.agent_type, kb_ids=body.kb_ids
    )
    db.add(session)
    await db.commit()
    return _session_dict(session)


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.user_id == user.id)
                .order_by(desc(ChatSession.updated_at))
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [_session_dict(s) for s in rows]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = await _own_session(db, user, session_id)
    msgs = (
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
    return {
        **_session_dict(s),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources or [],
                "run_id": m.run_id,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: SessionPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = await _own_session(db, user, session_id)
    if body.title is not None:
        s.title = body.title
    if body.agent_type is not None:
        s.agent_type = body.agent_type
    if body.kb_ids is not None:
        s.kb_ids = body.kb_ids
    await db.commit()
    return _session_dict(s)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _own_session(db, user, session_id)
    await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.delete(s)
    await db.commit()


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=32000)


@router.post("/sessions/{session_id}/messages", status_code=202)
async def post_message(
    session_id: str,
    body: MessageCreate,
    user: User = Depends(rate_limited("chat", "rate_limit_per_minute")),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    s = await _own_session(db, user, session_id)

    user_msg = ChatMessage(session_id=session_id, role="user", content=body.content)
    db.add(user_msg)
    if s.title == "新对话":
        s.title = body.content[:30]
    await db.commit()

    run_id = await container.run_manager.start(
        user_id=user.id,
        kind="chat",
        input={"message": body.content, "session_id": session_id},
        session_id=session_id,
        ctx=RunContext(),
        factory=make_chat_factory(container, s, body.content),
    )
    return {"run_id": run_id, "user_message_id": user_msg.id}
