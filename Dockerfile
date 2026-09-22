# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Non-root user setup
RUN groupadd -r appgroup && useradd -r -g appgroup -u 1000 appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY --chown=appuser:appgroup . /app

RUN mkdir -p /app/data/chroma /app/data/documents /app/logs \
    && chown -R appuser:appgroup /app/data /app/logs

USER appuser

CMD ["python", "main.py"]
