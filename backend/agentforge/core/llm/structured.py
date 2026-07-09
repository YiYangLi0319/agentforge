"""结构化输出：JSON Schema 注入 + 解析重试自纠错，返回 pydantic 模型实例。"""

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from agentforge.core.llm.base import ChatModel
from agentforge.core.messages import Message, Usage

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> str:
    """从模型输出中提取 JSON：优先取代码块，其次取首个 { 或 [ 到末尾配对段。"""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1)
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return text.strip()


async def complete_json[T: BaseModel](
    llm: ChatModel,
    messages: list[Message],
    schema: type[T],
    *,
    temperature: float | None = 0.1,
    max_retries: int = 2,
) -> tuple[T, Usage]:
    """要求模型输出符合 schema 的 JSON；失败时把报错回喂给模型自纠错重试。"""
    json_schema = schema.model_json_schema()
    instruction = (
        "\n\n请只输出一个合法的 JSON 对象（不要输出任何其他文字），符合以下 JSON Schema：\n"
        + json.dumps(json_schema, ensure_ascii=False)
    )
    convo = list(messages)
    if convo and convo[-1].role.value == "user":
        convo[-1] = Message.user(convo[-1].content + instruction)
    else:
        convo.append(Message.user(instruction))

    total_usage = Usage()
    last_err = ""
    for _ in range(max_retries + 1):
        resp = await llm.complete(
            convo,
            temperature=temperature,
            response_format={"type": "json_object"},
            schema_hint=json_schema,
        )
        total_usage = total_usage + resp.usage
        raw = resp.message.content
        try:
            data = json.loads(extract_json(raw))
            return schema.model_validate(data), total_usage
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = str(e)[:400]
            logger.warning("结构化输出解析失败，重试：%s", last_err)
            convo.append(Message.assistant(raw))
            convo.append(
                Message.user(f"上面的输出解析失败：{last_err}\n请重新只输出符合 Schema 的合法 JSON。")
            )
    raise ValueError(f"结构化输出解析失败（已重试 {max_retries} 次）：{last_err}")
