FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app NEWS_BACKEND_DATA=/data NEWS_BACKEND_REFRESH_SECONDS=900 NEWS_BACKEND_MAX_ARTICLES=8 NEWS_BACKEND_FEED_ITEM_LIMIT=4 DISCOVERY_SERVICE_SOURCES_FILE=/app/sources.json
WORKDIR /app
COPY discovery_service /app/discovery_service
COPY news_backend /app/news_backend
COPY sources.json /app/sources.json
EXPOSE 8791
CMD ["python", "-m", "news_backend", "serve"]
