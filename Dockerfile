FROM python:3.13-slim

WORKDIR /app

# requirements.txt alohida ko'chiriladi: kod o'zgarganda pip install qayta
# ishlamaydi, Docker keshdan oladi — build ancha tez bo'ladi.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY assistant ./assistant

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/assistant.db

# Eslatma: konteyner root sifatida ishlaydi. Non-root foydalanuvchiga o'tish
# xavfsizroq, lekin unda docker-compose'dagi ./data mount'iga yozish huquqini
# qo'lda berish kerak bo'ladi (chown). Shaxsiy bot uchun shu holat yetarli.
CMD ["python", "-m", "assistant.bot"]
