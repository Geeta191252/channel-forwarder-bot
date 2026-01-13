FROM python:3.11-slim

# Build context is repo root; bot source lives in /koyeb-bot
WORKDIR /app

# Install deps
COPY koyeb-bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY koyeb-bot/main.py ./main.py

EXPOSE 8000

# Run unbuffered so logs appear immediately
CMD ["python", "-u", "main.py"]
