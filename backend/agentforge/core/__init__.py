"""自研 Agent 引擎核心：不依赖 Web 框架与数据库，可独立复用。

分层说明：
- messages/events: 消息与事件协议（引擎的对外契约）
- llm: 模型供应商抽象（OpenAI 兼容 + Mock）
- tools: 工具注册与 JSON Schema 自动生成
- agent: ReAct 循环
- planner/supervisor: 计划执行与多 Agent 编排
- memory: 短期压缩记忆 + 长期向量记忆
- tracing: 全链路追踪
"""
