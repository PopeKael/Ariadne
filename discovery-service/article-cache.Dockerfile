FROM python:3.12-slim

WORKDIR /app
COPY discovery_service ./discovery_service
COPY cache_run.py ./cache_run.py

ENV ARTICLE_CACHE_SOURCE_DATABASE=/source/discovery.sqlite3
ENV ARTICLE_CACHE_INDEX_DATABASE=/cache/article-cache.sqlite3
ENV ARTICLE_CACHE_ROOT=/cache
ENV ARTICLE_CACHE_BIND_ADDRESS=0.0.0.0
ENV ARTICLE_CACHE_PORT=8790

VOLUME ["/source", "/cache"]
EXPOSE 8790

CMD ["python", "cache_run.py"]
