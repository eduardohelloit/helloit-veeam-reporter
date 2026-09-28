FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    openssl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/collectors/ ./scripts/collectors/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY ARCHITECTURE.md .
COPY start.sh /start.sh
RUN chmod +x /start.sh

RUN mkdir -p /data/uploads /data/reports /data/certs

ENV UPLOAD_DIR=/data/uploads
ENV REPORTS_DIR=/data/reports
ENV HTTPS_ONLY=true
ENV PYTHONUNBUFFERED=1

EXPOSE 8443

CMD ["/start.sh"]
