# One image for the two small Python services (sample app, AI analysis + review UI).
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY ai/ ai/
RUN useradd --system --uid 10001 service && mkdir /data && chown service /data
USER service

# Adds the test and lint toolchain. Used only by `docker compose run --rm tests`.
FROM runtime AS dev
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER service
ENV PYTHONPATH=/work
WORKDIR /work
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "app/tests", "ai/tests", "ci/tests"]
