"""集中配置：全部通过环境变量 / .env 注入，支持多厂商 LLM 一键切换。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 应用
    env: str = Field(default="dev", alias="AGENTFORGE_ENV")
    secret_key: str = "dev-secret-change-me"
    jwt_expire_hours: int = 168
    # 注册邀请码：留空=开放注册；设置后注册必须提供正确邀请码（防止陌生人消耗额度）
    registration_invite_code: str = ""

    # 存储
    database_url: str = "sqlite+aiosqlite:///./agentforge.db"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 20
    # 前端静态资源目录（存在则由后端同源托管，用于单镜像部署）
    static_dir: str = "static"
    # 是否尝试使用 pgvector（PostgreSQL）；不可用会自动降级为 JSON+进程内向量检索
    use_pgvector: bool = True

    # 对话模型
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.3

    # 评审模型（LLM-as-judge），不填则复用对话模型
    judge_provider: str = ""
    judge_api_key: str = ""
    judge_model: str = ""
    judge_base_url: str = ""

    # Embedding
    embedding_provider: str = "mock"
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_dim: int = 1024

    # 外部检索服务
    search_provider: str = "auto"  # auto | mock（auto: Tavily > DuckDuckGo > Mock 兜底）
    tavily_api_key: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_base_url: str = ""

    # Agent 运行参数
    agent_max_steps: int = 8
    agent_token_budget: int = 40000
    research_max_workers: int = 3
    research_max_sources: int = 12
    research_max_revisions: int = 2  # 报告未达标时的最大迭代修订轮数（Reflexion 循环）
    max_concurrent_runs: int = 8
    max_concurrent_runs_per_user: int = 2
    max_concurrent_runs_per_session: int = 1
    sandbox_enabled: bool = False
    sandbox_timeout: int = 20
    sandbox_requires_approval: bool = True
    custom_http_tools_enabled: bool = False
    chat_history_token_budget: int = 6000

    # 限流
    rate_limit_per_minute: int = 60
    rate_limit_research_per_minute: int = 5

    # 安全护栏
    guardrails_enabled: bool = True
    guardrails_block_injection: bool = True  # 检测到 prompt 注入即拦截
    guardrails_mask_pii: bool = True  # 输出中的 PII 脱敏
    guardrails_moderation: bool = True  # 输入内容审核

    # 语义缓存
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.93
    semantic_cache_ttl_seconds: int = 86400

    # RAG 进阶（默认开父子分块；改写/HyDE/压缩会增加 LLM 调用，默认关，可按需开）
    rag_query_rewrite: bool = False
    rag_hyde: bool = False
    rag_compression: bool = False
    rag_parent_child: bool = True
    # JSON 降级模式（无 pgvector）下向量检索的进程内全量扫描上限；
    # 超过则改用 BM25 候选预筛，避免把整库 embedding 载入内存排序。
    rag_json_scan_limit: int = 5000

    # MCP：JSON 配置文件路径（描述要接入的 MCP 服务器），为空则不启用
    mcp_config_path: str = ""

    # 多用户运营
    admin_username: str = ""  # 该用户名注册/登录后自动成为管理员
    daily_token_quota: int = 200000  # 每用户每日 token 额度（0=不限）；管理员不受限
    max_dataset_rows: int = 5000  # 单个上传数据集最大行数

    # CORS
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
    ]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    def validate_runtime(self) -> None:
        """在装配外部依赖前执行生产安全门禁，避免危险默认值静默上线。"""
        if not self.is_production:
            return
        if self.secret_key == "dev-secret-change-me" or len(self.secret_key) < 32:
            raise ValueError("生产环境 SECRET_KEY 必须是至少 32 位的随机字符串")
        if self.llm_provider == "mock":
            raise ValueError("生产环境禁止使用 Mock LLM，请配置真实 LLM_PROVIDER 与 API Key")
        if self.llm_provider not in {"deepseek", "qwen", "openai", "glm", "moonshot", "custom"}:
            raise ValueError(f"生产环境不接受未知 LLM_PROVIDER={self.llm_provider}")
        if not self.llm_api_key:
            raise ValueError("生产环境必须配置 LLM_API_KEY，禁止静默降级为 Mock")
        if self.llm_provider == "custom" and not self.llm_base_url:
            raise ValueError("LLM_PROVIDER=custom 时必须配置 LLM_BASE_URL")
        if self.sandbox_enabled:
            raise ValueError("内置 Python 执行器不具备容器级隔离，生产环境必须设置 SANDBOX_ENABLED=false")
        if self.admin_username and not self.registration_invite_code:
            raise ValueError(
                "生产环境配置 ADMIN_USERNAME 时必须同时设置 REGISTRATION_INVITE_CODE，防止管理员用户名被抢注"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
