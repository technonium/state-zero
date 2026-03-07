# State Zero - Dokploy Deployment
# Python 3.11 with ffmpeg for video processing

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    openssh-client \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for runtime data (if needed)
# These can be mounted as volumes from the host
RUN mkdir -p /opt/state-zero-private/astrology \
    /opt/state-zero-private/runtime/database \
    /opt/state-zero-private/runtime/output \
    /opt/state-zero-private/runtime/state

# Set default environment variables
ENV PYTHONUNBUFFERED=1
ENV STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-private
ENV PIPELINE_MEDIA_MODE=live_vps

# Default command (can be overridden in Dokploy)
CMD ["python3", "-u", "src/scripts/pipeline.py"]
