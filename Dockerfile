# ─── StreamCraft Backend — Render Deployment ───────────────────────────────────
# Python 3.11 slim image with ffmpeg, curl, and git
FROM python:3.11-slim

# Install system dependencies (ffmpeg for transcoding/merging, curl and git for builds)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Upgrade pip, wheel, hatchling for building git packages
RUN pip install --no-cache-dir -U pip setuptools wheel hatchling

# Copy and install Python dependencies
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Force reinstall latest yt-dlp from master branch directly to ensure cutting-edge fixes
RUN pip install --no-cache-dir --force-reinstall "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"

# Copy the API source code
COPY api/ ./api/

# Expose the port Render binds to
EXPOSE 8000

# Start FastAPI application with uvicorn
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
