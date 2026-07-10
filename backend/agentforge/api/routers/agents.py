"""自定义 Agent 构建器：用户自建 Agent（人设 + 工具 + 知识库），运行时动态构建。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.db.models import CustomAgent, KnowledgeBase, User

router = APIRouter()

# 可供自定义 Agent 选择的内置工具（名称 -> 说明）
SELECTABLE_TOOLS: dict[str, str] = {
    "search_knowledge_base": "检索绑定的企业知识库并带引用",
    "web_search": "联网搜索实时信息",
    "web_fetch": "抓取网页正文",
    "calculator": "精确数学计算",
    "current_time": "获取当前时间",
    "python_execute": "沙箱执行 Python（需审批）",
}


class CustomAgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=8000)
    tools: list[str] = Field(default_factory=list)
    kb_ids: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=8, ge=1, le=20)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)


def _dict(a: CustomAgent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "system_prompt": a.system_prompt,
        "tools": a.tools,
        "kb_ids": a.kb_ids,
        "max_steps": a.max_steps,
        "temperature": a.temperature,
        "created_at": a.created_at.isoformat(),
    }


async def _validate(db: AsyncSession, user: User, body: CustomAgentIn, container: Container) -> None:
    bad_tools = [t for t in body.tools if t not in SELECTABLE_TOOLS]
    if bad_tools:
        raise HTTPException(status_code=400, detail=f"未知工具: {', '.join(bad_tools)}")
    if "python_execute" in body.tools and not container.settings.sandbox_enabled:
        raise HTTPException(status_code=400, detail="当前环境未启用 Python 执行器")
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


@router.get("/tools")
async def list_selectable_tools(
    user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    return [
        {"name": k, "description": v}
        for k, v in SELECTABLE_TOOLS.items()
        if k != "python_execute" or container.settings.sandbox_enabled
    ]


@router.get("")
async def list_agents(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(CustomAgent).where(CustomAgent.user_id == user.id).order_by(desc(CustomAgent.created_at))
            )
        )
        .scalars()
        .all()
    )
    return [_dict(a) for a in rows]


@router.post("", status_code=201)
async def create_agent(
    body: CustomAgentIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    await _validate(db, user, body, container)
    agent = CustomAgent(
        user_id=user.id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        tools=body.tools,
        kb_ids=body.kb_ids,
        max_steps=body.max_steps,
        temperature=body.temperature,
    )
    db.add(agent)
    await db.commit()
    return _dict(agent)


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: CustomAgentIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    agent = (
        await db.execute(select(CustomAgent).where(CustomAgent.id == agent_id, CustomAgent.user_id == user.id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    await _validate(db, user, body, container)
    agent.name = body.name
    agent.description = body.description
    agent.system_prompt = body.system_prompt
    agent.tools = body.tools
    agent.kb_ids = body.kb_ids
    agent.max_steps = body.max_steps
    agent.temperature = body.temperature
    await db.commit()
    return _dict(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    agent = (
        await db.execute(select(CustomAgent).where(CustomAgent.id == agent_id, CustomAgent.user_id == user.id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    await db.delete(agent)
    await db.commit()
