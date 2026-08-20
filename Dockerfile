# ─── StreamCraft Backend — Render Deployment ───────────────────────────────────
# Slim Python 3.11 image with ffmpeg pre-installed for audio conversion
FROM python:3.11-slim

# Install ffmpeg + curl (for yt-dlp self-update) in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy and install Python dependencies first (cached layer)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Force-upgrade yt-dlp to latest at build time (bypasses stale PyPI version)
RUN pip install --no-cache-dir --upgrade yt-dlp

# Copy the API source code
COPY api/ ./api/

# Expose the port Render will bind to
EXPOSE 8000

# Start FastAPI with uvicorn
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
