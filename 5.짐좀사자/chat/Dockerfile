FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Seoul DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m app
USER app
# DuckDB SQLite·JSON 확장을 빌드 시점에 설치 (런타임엔 외부 접근 차단)
RUN python -c "import duckdb; [duckdb.sql(f'INSTALL {e}') for e in ('sqlite','json')]"
COPY --chown=app *.py ./
COPY --chown=app static ./static
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
