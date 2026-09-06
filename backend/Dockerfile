FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Requirements first so dependency layers survive source-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# seat_lock.py reads seat_lock.lua from disk at import time, so the script
# has to ship in the image alongside the modules.
COPY . .

RUN useradd --create-home --uid 10001 velora
USER velora

EXPOSE 8080

# Render injects PORT. The fallback keeps the image runnable under a plain
# `docker run` with no environment set.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
