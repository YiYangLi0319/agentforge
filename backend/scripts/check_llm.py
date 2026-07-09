"""连通性自检：用当前 .env 配置真实调用对话模型与 Embedding，确认 Key 是否有效。

用法（backend 目录下）：
    python scripts/check_llm.py

只打印状态与用量，不回显 API Key。
"""

import asyncio
import sys

from agentforge.config import get_settings
from agentforge.core.llm.registry import build_chat_model, build_embeddings
from agentforge.core.messages import Message

# Windows 控制台默认 GBK，强制 UTF-8 避免中文/符号打印报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


async def main() -> int:
    s = get_settings()
    chat = build_chat_model(s)
    emb = build_embeddings(s)
    print(f"对话模型: provider={chat.provider} model={chat.model}")
    print(f"Embedding: provider={emb.provider} model={emb.model}")
    print("-" * 48)

    ok = True

    # 对话模型真实调用
    if chat.provider == "mock":
        print("[对话] Mock 模式（未配置真实 Key），跳过真实调用")
    else:
        try:
            resp = await chat.complete([Message.user("用一句话中文自我介绍")], max_tokens=60)
            print(f"[对话] OK  回复: {resp.message.content[:60]}")
            print(f"        用量: prompt={resp.usage.prompt_tokens} completion={resp.usage.completion_tokens}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[对话] 失败  {type(e).__name__}: {str(e)[:200]}")

    # Embedding 真实调用
    if emb.provider == "mock":
        print("[Embedding] Mock 模式（DeepSeek 无 embedding，属正常）")
    else:
        try:
            vecs = await emb.embed(["连通性测试"])
            print(f"[Embedding] OK  维度={len(vecs[0])}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[Embedding] 失败  {type(e).__name__}: {str(e)[:200]}")

    print("-" * 48)
    print("结论:", "全部可用" if ok else "存在失败，请检查 Key / 网络")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
