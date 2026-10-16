FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul \
    DATA_DIR=/data \
    CACHE_DIR=/data/cache \
    OUT_DIR=/output

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py briefing_template.html ./

VOLUME ["/data", "/output"]
CMD ["python", "scheduler.py"]
