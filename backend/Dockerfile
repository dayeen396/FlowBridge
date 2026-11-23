FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app/backend

RUN useradd --create-home --uid 10001 flowbridge

COPY --chown=flowbridge:flowbridge requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=flowbridge:flowbridge app ./app
COPY --chown=flowbridge:flowbridge migrations ./migrations
COPY --chown=flowbridge:flowbridge alembic.ini .

EXPOSE 8000
USER flowbridge
