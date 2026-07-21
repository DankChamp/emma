# Emma headless server image for Hugging Face Spaces (Docker Space).
# HF exposes the app on port 7860 and runs the container as a non-root uid.
FROM python:3.12-slim

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY config.py main.py ./
COPY api ./api
COPY core ./core
COPY web ./web

# HF Spaces runs with an arbitrary non-root uid: data/, .env (settings API
# writes to it), and the huggingface_hub cache must all be writable.
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p /app/data /app/.cache/huggingface && touch /app/.env \
    && chmod -R 777 /app/data /app/.cache /app/.env

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
