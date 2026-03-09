FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

LABEL org.opencontainers.image.description="Track Spotify playlist changes, diffs, and summary webhooks."

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install .

VOLUME ["/app/results"]

CMD ["spotify-playlist-tracker", "run"]