# syntax=docker/dockerfile:1
# 排练通知 QQ Bot 应用镜像（NapCat 由 docker-compose 单独启动）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    REHEARSAL_DB_PATH=/data/rehearsal.db \
    REHEARSAL_HEARTBEAT_PATH=/data/heartbeat \
    REHEARSAL_LOG_DIR=/logs

WORKDIR /app

# 依赖版本由 uv.lock 导出为 requirements.txt，保证可复现
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY pyproject.toml ./
COPY bot.py ./
COPY rehearsal ./rehearsal
COPY plugins ./plugins
COPY scripts ./scripts

RUN mkdir -p /data /logs

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

CMD ["python", "bot.py"]
