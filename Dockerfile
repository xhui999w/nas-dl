FROM denoland/deno:bin-2.8.1 AS deno

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/xhui999w/nas-dl"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NASFLOW_DATA=/data \
    NASFLOW_DOWNLOADS=/downloads
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8888/api/health || exit 1
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8888"]
