FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TF_CPP_MIN_LOG_LEVEL=3 \
    TF_ENABLE_ONEDNN_OPTS=0 \
    GRADIO_ANALYTICS_ENABLED=False \
    OUT_DIR=/app/outputs \
    PORT=7860

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY model/ ./model/
COPY codes.json model.py collector.py app.py ./
RUN mkdir -p /app/outputs

EXPOSE 7860
CMD ["python", "app.py"]
