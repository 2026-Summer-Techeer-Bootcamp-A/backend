# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runner
FROM python:3.12-slim AS runner

WORKDIR /app

# Copy site-packages and binaries from builder
COPY --from=builder /install /usr/local

COPY app/ app/
COPY static/ static/
COPY templates/ templates/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN useradd --create-home --shell /usr/sbin/nologin appuser
RUN chown -R appuser:appuser /app

# docker-compose.yml이 마운트하는 model-cache:/models 명명 볼륨은, 이미지 안에 /models가
# 없으면 Docker가 최초 마운트 시 root:root 소유로 초기화한다(명명 볼륨이 비어있을 때
# 이미지 쪽 경로의 소유권/권한을 그대로 복사하는 동작). appuser로 실행되는 컨테이너가
# 그 볼륨에 BGE-M3 모델을 내려받지 못해 임베딩이 조용히 실패했던 원인이 이것이었다.
# 이미지에 appuser 소유의 /models를 미리 만들어 두면 볼륨도 appuser 소유로 초기화된다.
RUN mkdir -p /models && chown -R appuser:appuser /models

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]

