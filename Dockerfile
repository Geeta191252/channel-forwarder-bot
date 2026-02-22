FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
# IMPORTANT: the full-feature bot lives in koyeb-bot/main.py
COPY koyeb-bot/main.py ./main.py
COPY koyeb-bot/translations.py ./translations.py

EXPOSE 8000

# Run unbuffered so logs appear immediately
CMD ["python", "-u", "main.py"]
