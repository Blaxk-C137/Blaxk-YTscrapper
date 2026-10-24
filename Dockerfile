FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CREATORSCOPE_DATA_DIR=/tmp/creatorscope

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" \
    && playwright install --with-deps chromium \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

COPY . .
RUN useradd --create-home --uid 10001 creatorscope \
    && mkdir -p /tmp/creatorscope /app/exports \
    && chown -R creatorscope:creatorscope /tmp/creatorscope /app/exports
USER creatorscope

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/api/health')" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
