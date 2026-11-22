FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINANCE_DB=/data/finance.db

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as an unprivileged user; /data holds the SQLite database (mount a volume here).
RUN useradd --uid 1000 --create-home finance && mkdir -p /data && chown finance:finance /data
USER finance
VOLUME ["/data"]

EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--proxy-headers"]
