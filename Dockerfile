FROM python:3.11-slim

WORKDIR /app

# Install dependencies from koyeb-bot folder
COPY koyeb-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY koyeb-bot/main.py .

# Expose port
EXPOSE 8000

# Run unbuffered so startup logs appear immediately
CMD ["python", "-u", "main.py"]
