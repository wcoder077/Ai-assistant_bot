FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY assistant ./assistant

ENV PYTHONUNBUFFERED=1 DB_PATH=/app/data/assistant.db

CMD ["python", "-m", "assistant.bot"]
