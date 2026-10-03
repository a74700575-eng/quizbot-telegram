FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DB_PATH=/data/quizbot.db

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY bot.py /app/bot.py
COPY quizbot/ /app/quizbot/

CMD ["python", "-u", "/app/bot.py"]
