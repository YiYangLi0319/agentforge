FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY agentforge ./agentforge
COPY alembic.ini ./
COPY alembic ./alembic
COPY samples ./samples

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "agentforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
