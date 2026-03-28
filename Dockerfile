FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional: install demucs (background music separation)
# Uncomment the next line if you have enough memory (requires ~1GB+ RAM)
# RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir demucs>=4.0.0

# Copy app files
COPY . .

# Create necessary directories
RUN mkdir -p temp output

EXPOSE 8080

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "1", "--threads", "4"]
