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

EXPOSE 8000

# PaaS 通过 $PORT 指定端口；本地默认 8000
CMD ["sh", "-c", "uvicorn agentforge.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
