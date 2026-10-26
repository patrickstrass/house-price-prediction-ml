#!/bin/sh
set -eu

: "${MLFLOW_BACKEND_STORE_URI:?set MLFLOW_BACKEND_STORE_URI}"
: "${MLFLOW_ALLOWED_HOSTS:?set MLFLOW_ALLOWED_HOSTS}"

exec mlflow server \
  --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --artifacts-destination /mlflow-artifacts \
  --serve-artifacts \
  --host 0.0.0.0 \
  --port 5000 \
  --workers 1 \
  --allowed-hosts "$MLFLOW_ALLOWED_HOSTS"
