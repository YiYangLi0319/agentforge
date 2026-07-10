# ============ 单镜像部署：Node 构建前端 -> Python 托管 API + 前端 ============
# 适用于 Railway / Render / Zeabur / Fly.io 等任何支持 Docker 的 PaaS。

# ---------- 阶段 1：构建前端 ----------
FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：Python 后端 + 托管前端 ----------
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000

COPY backend/pyproject.toml ./
COPY backend/agentforge ./agentforge
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/samples ./samples

RUN pip install --no-cache-dir .

# 前端构建产物 -> 后端同源托管目录
COPY --from=frontend /web/dist ./static

RUN groupadd --system agentforge \
    && useradd --system --gid agentforge --home-dir /app agentforge \
    && chown -R agentforge:agentforge /app

USER agentforge

EXPOSE 8000

# 单实例启动：先兼容遗留库并迁移，再启动 API。当前事件总线/审批所有权为进程内实现，不支持多 worker。
CMD ["sh", "-c", "python -m agentforge.db.bootstrap && uvicorn agentforge.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
