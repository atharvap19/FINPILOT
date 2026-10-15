# FinPilot API
#
# One process on purpose (--workers 1): upload jobs run on an in-process
# thread pool, PDF passwords are held in that process's memory only, and the
# database is SQLite. Scale up the machine, not the worker count.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    HOME=/home/app \
    DATA_DIR=/data

# DejaVu Sans has the ₹ glyph the PDF report needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --home-dir /home/app --uid 10001 app

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
ENV PATH=/opt/venv/bin:$PATH

# Bake the local embedding model (all-MiniLM-L6-v2, ONNX) into the image so
# the first upload doesn't wait on a download, then hand the app its folders:
# /data for the database, uploads, exports and vectors; /app because learned
# column templates and the demo fixtures are written beside the code.
RUN mkdir -p /data \
    && python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm up'])" \
    && chown -R app:app /data /app /home/app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
