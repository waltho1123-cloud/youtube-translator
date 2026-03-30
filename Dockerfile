FROM python:3.11-slim

# Install system dependencies (Node.js needed by yt-dlp for YouTube NSIG challenge)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    nodejs \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Ensure Python output is sent straight to stdout/stderr without buffering
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install demucs for background music separation (CPU-only PyTorch)
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir demucs>=4.0.0

# Copy app files
COPY . .

# Create necessary directories
RUN mkdir -p temp output

EXPOSE 8080

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "1", "--threads", "4", "--access-logfile", "-", "--log-level", "info"]
