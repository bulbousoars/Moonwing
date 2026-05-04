FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
COPY alembic.ini .
COPY alembic/ alembic/

RUN pip install --no-cache-dir . uvicorn[standard]

FROM python:3.13-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/src /app/src
COPY --from=builder /build/alembic.ini /app/alembic.ini
COPY --from=builder /build/alembic /app/alembic

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Default to API; worker overrides command in compose
CMD ["python", "-m", "uvicorn", "moonwing.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
