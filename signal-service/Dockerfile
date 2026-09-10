FROM python:3.12-slim

WORKDIR /app
COPY signal_service ./signal_service
COPY run.py ./run.py

ENV SIGNAL_SERVICE_BIND_ADDRESS=0.0.0.0
ENV SIGNAL_SERVICE_PORT=8788
ENV SIGNAL_SERVICE_DATABASE=/data/signals.sqlite3
ENV SIGNAL_SERVICE_IMAGE_TIMEOUT_SECONDS=5
# On Hera, Discovery Engine is the sole collection owner; Signal Service only
# receives, ranks, and presents the materialized intake.
ENV SIGNAL_SERVICE_DISABLE_BUILTIN_FEEDS=true
VOLUME ["/data"]
EXPOSE 8788

CMD ["python", "run.py"]
