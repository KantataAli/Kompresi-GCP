FROM python:3.11-slim

# Install system dependencies: FFmpeg (video/audio) + Ghostscript (PDF)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Cloud Run injects PORT env variable (default 8080)
ENV PORT=8080

CMD ["python", "app.py"]
