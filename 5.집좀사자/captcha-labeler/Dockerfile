FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    TF_CPP_MIN_LOG_LEVEL=2 \
    DATA_DIR=/data \
    NO_PROXY=localhost,127.0.0.1,0.0.0.0

# gen_captcha.py 용 폰트
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py gen_captcha.py ./

VOLUME ["/data"]
EXPOSE 7860
CMD ["python", "app.py"]
