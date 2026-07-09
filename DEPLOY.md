# 部署指南（PaaS 一键上线）

本项目已打包为**单个 Docker 镜像**：后端同源托管前端，一个服务即可对外提供完整站点。任何支持 Docker 的 PaaS 都能直接部署，无需拆分前后端、无跨域配置。

> 已本地验证：`docker build` 成功、容器读取 `$PORT` 正常、`/` 出前端、`/api/*` 出接口。

## 一、先推送到 GitHub

1. 在 GitHub 网页新建一个**空仓库**（不要勾选 README/.gitignore），拿到仓库地址，例如 `https://github.com/你的用户名/agentforge.git`
2. 在项目根目录 `C:\Users\lyy\Projects\agentforge` 执行（Git 已初始化）：

```bash
git add .
git commit -m "chore: initial commit for deployment"
git branch -M main
git remote add origin https://github.com/你的用户名/agentforge.git
git push -u origin main
```

> `.env`（含你的 DeepSeek Key）已被 `.gitignore` 忽略，不会上传。密钥统一在 PaaS 面板的环境变量里配置。

## 二、需要在 PaaS 配置的环境变量

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 一段随机长字符串 | JWT 签名，务必自定义 |
| `LLM_PROVIDER` | `deepseek` | 对话模型厂商 |
| `LLM_API_KEY` | `sk-你的DeepSeekKey` | **必填** |
| `LLM_MODEL` | `deepseek-chat` | |
| `EMBEDDING_PROVIDER` | `mock` | 无 embedding key 时保持 mock |
| `SEARCH_PROVIDER` | `auto` | 深度研究联网搜索 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./agentforge.db` | 免费档用 SQLite；要持久化见下方 |
| `REGISTRATION_INVITE_CODE` | 自定义一段口令 | **公开部署强烈建议设置**：只有知道邀请码的人能注册，防止陌生人消耗你的模型额度 |

> 端口无需设置：PaaS 会注入 `$PORT`，镜像已自动适配。

## 三、三选一：具体平台步骤

### 方案 A：Render（有真正的免费档，推荐先用它）

- 免费 Web Service：15 分钟无访问会休眠，再次访问 ~30s 冷启动（演示够用）。
- 步骤：
  1. 登录 [render.com](https://render.com) → New + → **Blueprint** → 选中你的仓库（会自动读取根目录 `render.yaml`）
  2. 或 New + → **Web Service** → 选仓库 → Runtime 选 **Docker** → 其余默认
  3. 在 Environment 里填 `LLM_API_KEY`（其余变量 `render.yaml` 已预置）
  4. Create → 等构建完成，拿到 `https://agentforge-xxx.onrender.com`

### 方案 B：Railway（体验最顺，按用量计费/有起始额度）

1. 登录 [railway.app](https://railway.app) → New Project → **Deploy from GitHub repo** → 选你的仓库
2. Railway 自动识别根目录 `Dockerfile`（`railway.json` 已配好健康检查）
3. Variables 里逐条填上表环境变量
4. Settings → Networking → **Generate Domain**，拿到公网地址

### 方案 C：Zeabur（国内访问友好）

1. 登录 [zeabur.com](https://zeabur.com) → 新建项目 → Deploy from GitHub → 选仓库
2. 自动识别 Dockerfile 构建
3. 在环境变量里填上表变量
4. Networking → 绑定一个 `.zeabur.app` 域名

## 四、数据持久化（可选）

免费档多为**临时文件系统**：重新部署/重启后 SQLite 数据会重置（账号、知识库、历史会清空）。要持久化，二选一：

- **加挂载盘**：Render/Railway 付费实例可挂 Disk，把 `DATABASE_URL` 指到盘内路径，如 `sqlite+aiosqlite:////data/agentforge.db`
- **用托管 PostgreSQL**（推荐生产）：在平台加一个 Postgres 服务，把 `DATABASE_URL` 设为其连接串（`postgresql+asyncpg://...`）。
  - 若要真实向量检索，需数据库支持 `pgvector`（Render 的 Postgres 已内置；否则应用会自动降级为进程内向量计算，功能不受影响）。

## 五、上线后注意

- **成本**：站点公开后，所有访客用的是**你的 DeepSeek 额度**。演示完可在平台暂停服务或删除域名。
- **安全**：设置了 `REGISTRATION_INVITE_CODE` 后，只有知道邀请码的人才能注册（登录页会自动出现邀请码输入框）。把邀请码只发给你信任的人即可。
- **首次访问**：注册账号 → 知识库页「导入演示样例」→ 即可体验带引用的问答与深度研究。

## 六、本地用 Docker 跑同款镜像（自测）

```bash
docker build -t agentforge:latest .
docker run --rm -p 8080:8000 -e PORT=8000 -e LLM_PROVIDER=deepseek -e LLM_API_KEY=sk-xxx -e SECRET_KEY=any-long-random .
# 浏览器打开 http://127.0.0.1:8080
```
