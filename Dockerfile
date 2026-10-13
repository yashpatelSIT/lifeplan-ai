FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY lifeplan ./lifeplan
COPY web ./web
COPY samples ./samples
COPY scripts ./scripts

RUN useradd --create-home appuser && mkdir -p /app/data /app/traces && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health')"
CMD ["sh", "-c", "uvicorn lifeplan.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
