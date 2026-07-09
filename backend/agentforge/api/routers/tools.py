"""工具管理：内置/ MCP 工具列表 + 自定义 HTTP 工具 CRUD 与测试调用。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.core.tools.builtins import calculator, current_time
from agentforge.core.tools.python_sandbox import python_execute
from agentforge.core.tools.retrieval import search_knowledge_base
from agentforge.core.tools.web_fetch import web_fetch
from agentforge.core.tools.web_search import web_search
from agentforge.db.models import CustomTool, User
from agentforge.services.custom_tools import build_custom_tool

router = APIRouter()

_BUILTINS = [search_knowledge_base, web_search, web_fetch, calculator, current_time, python_execute]


class ParamSpec(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    type: str = Field(default="string", pattern="^(string|number|integer|boolean)$")
    required: bool = True
    description: str = ""
    location: str = Field(default="query", pattern="^(query|path|body)$")


class CustomToolIn(BaseModel):
    name: str = Field(min_length=2, max_length=40, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")
    description: str = Field(default="", max_length=500)
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|DELETE|PATCH)$")
    url_template: str = Field(min_length=8, max_length=1000)
    headers: dict = Field(default_factory=dict)
    params_schema: list[ParamSpec] = Field(default_factory=list)
    body_template: str = ""
    enabled: bool = True
    timeout: int = Field(default=15, ge=1, le=60)


@router.get("/builtin")
async def list_builtin_tools(user: User = Depends(get_current_user)) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "requires_approval": t.requires_approval,
            "tags": t.tags,
            "parameters": t.parameters,
        }
        for t in _BUILTINS
    ]


@router.get("/mcp")
async def list_mcp(
    user: User = Depends(get_current_user), container: Container = Depends(get_container)
) -> dict:
    return {"status": container.mcp.status, "tools": container.mcp.tool_list()}


def _dict(t: CustomTool) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "method": t.method,
        "url_template": t.url_template,
        "headers": t.headers,
        "params_schema": t.params_schema,
        "body_template": t.body_template,
        "enabled": t.enabled,
        "timeout": t.timeout,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/custom")
async def list_custom_tools(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (await db.execute(select(CustomTool).where(CustomTool.user_id == user.id).order_by(CustomTool.created_at)))
        .scalars()
        .all()
    )
    return [_dict(t) for t in rows]


@router.post("/custom", status_code=201)
async def create_custom_tool(
    body: CustomToolIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    exists = (
        await db.execute(
            select(CustomTool).where(CustomTool.user_id == user.id, CustomTool.name == body.name)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="同名工具已存在")
    row = CustomTool(
        user_id=user.id,
        name=body.name,
        description=body.description,
        method=body.method,
        url_template=body.url_template,
        headers=body.headers,
        params_schema=[p.model_dump() for p in body.params_schema],
        body_template=body.body_template,
        enabled=body.enabled,
        timeout=body.timeout,
    )
    db.add(row)
    await db.commit()
    return _dict(row)


@router.patch("/custom/{tool_id}")
async def update_custom_tool(
    tool_id: str,
    body: CustomToolIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await db.execute(select(CustomTool).where(CustomTool.id == tool_id, CustomTool.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工具不存在")
    row.name = body.name
    row.description = body.description
    row.method = body.method
    row.url_template = body.url_template
    row.headers = body.headers
    row.params_schema = [p.model_dump() for p in body.params_schema]
    row.body_template = body.body_template
    row.enabled = body.enabled
    row.timeout = body.timeout
    await db.commit()
    return _dict(row)


@router.delete("/custom/{tool_id}", status_code=204)
async def delete_custom_tool(
    tool_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(select(CustomTool).where(CustomTool.id == tool_id, CustomTool.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工具不存在")
    await db.delete(row)
    await db.commit()


class ToolTestIn(BaseModel):
    arguments: dict = Field(default_factory=dict)


@router.post("/custom/{tool_id}/test")
async def test_custom_tool(
    tool_id: str,
    body: ToolTestIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await db.execute(select(CustomTool).where(CustomTool.id == tool_id, CustomTool.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工具不存在")
    tool = build_custom_tool(row)
    result = await tool.execute(body.arguments)
    return {"ok": result.ok, "content": result.content[:2000]}
