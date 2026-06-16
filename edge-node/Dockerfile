FROM python:3.10-slim

# Install system dependencies including FFmpeg and OpenGL libs for OpenCV
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command can be overridden by docker-compose
CMD ["python", "capture_stream.py", "--server", "http://server:5000", "--config", "../configs/balanced.yaml"]
