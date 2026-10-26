FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim AS runtime
ARG IMAGE_REVISION=local-dev
LABEL org.opencontainers.image.revision=$IMAGE_REVISION
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRAINING_ENVIRONMENT_VERSION=$IMAGE_REVISION
RUN apt-get update \
    && apt-get install --no-install-recommends --yes postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app/data /model-registry \
    && chown -R app:app /app /model-registry
COPY --from=builder /opt/venv /opt/venv
COPY data /app/data
COPY --chown=app:app requirements.lock /app/requirements.lock
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app migrations /app/migrations
COPY --chown=app:app scripts/reconcile_catalog.sh /usr/local/bin/reconcile-catalog
COPY --chown=app:app scripts/azure_backup_job.sh /usr/local/bin/azure-backup-job
COPY --chown=app:app scripts/azure_storage_probe.py /usr/local/bin/azure-storage-probe
COPY --chown=app:app scripts/azure_storage_evidence.py /usr/local/bin/azure-storage-evidence
RUN chmod 0555 /usr/local/bin/reconcile-catalog /usr/local/bin/azure-backup-job \
    /usr/local/bin/azure-storage-probe /usr/local/bin/azure-storage-evidence
RUN chmod -R a-w /app/data
USER 10001:10001
WORKDIR /app
VOLUME ["/model-registry"]
CMD ["train"]

