FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim AS runtime
ARG IMAGE_REVISION=local-dev
LABEL org.opencontainers.image.revision=$IMAGE_REVISION
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT_VERSION=$IMAGE_REVISION
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app /model-registry \
    && chown -R app:app /app /model-registry
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app requirements.lock /app/requirements.lock
COPY --chown=app:app scripts/azure_storage_evidence.py /usr/local/bin/azure-storage-evidence
USER 10001:10001
WORKDIR /app
EXPOSE 8000
VOLUME ["/model-registry"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
CMD ["uvicorn", "house_price.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

