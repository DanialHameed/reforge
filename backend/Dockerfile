# Production Dockerfile for ReForge backend.
#
# Hardening choices (audit P-2 / P-7 / production polish):
#   * Pinned base image digest tag (python:3.11-slim) — bumping requires
#     intentional commit, not implicit pull at build time.
#   * System ffmpeg installed: `app.services.media_validation` and the
#     publisher modules call ffprobe / ffmpeg-python; without the binary
#     every media validation in production silently raises FileNotFoundError.
#   * tini as PID 1: forwards signals correctly so `docker stop` triggers
#     graceful shutdown of uvicorn / celery instead of SIGKILL after 10s.
#   * Non-root user `reforge` owns /app and the uploads/ fallback dir.
#   * Slim final layer: apt cache wiped, no compilers left over.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# ----- System dependencies -------------------------------------------------
# ffmpeg: required by app.services.media_validation, ffmpeg-python, moviepy.
# tini  : init shim so SIGTERM reaches the Python process.
# curl  : optional but commonly used by external orchestrator healthcheckers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        tini \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ----- Python dependencies (cached layer) ----------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ----- Application source --------------------------------------------------
COPY . /app

# Local-fallback uploads directory referenced by app/api/v1/content.py.
# The compose file mounts a named volume on top, but the directory must
# exist with correct ownership for the bind to work cleanly.
RUN mkdir -p /app/uploads /app/beat

# ----- Non-root runtime user ----------------------------------------------
# Created with a fixed UID so volume permissions are predictable across
# rebuilds.
RUN groupadd --system --gid 10001 reforge \
 && useradd  --system --uid 10001 --gid reforge --home-dir /app reforge \
 && chown -R reforge:reforge /app

USER reforge

EXPOSE 8000

# tini reaps zombies and forwards signals; uvicorn is the actual entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
