#!/bin/sh
set -eu
exec python -m house_price.azure.backup_job
