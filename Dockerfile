FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by paho-mqtt / sqlite (sqlite3 is built into Python,
# but build tools are sometimes needed for transitive deps on slim images)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot.py db.py .

# /data and /var/log are volume-mounted by docker-compose; create them so the
# app doesn't fail on first run if the host directories were empty.
RUN mkdir -p /data /var/log

CMD ["python", "telegram_bot.py"]
