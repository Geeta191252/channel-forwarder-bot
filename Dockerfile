FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY main.py .

EXPOSE 8000

# Run unbuffered so logs appear immediately
CMD ["python", "-u", "main.py"]
