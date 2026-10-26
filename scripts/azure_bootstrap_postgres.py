"""Run the idempotent role/schema bootstrap against a future private PostgreSQL endpoint."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("bootstrap command validated only; --execute is required later")
        return 0
    required = ("PGPASSWORD", "MIGRATION_PASSWORD", "PIPELINE_PASSWORD", "GRAFANA_PASSWORD")
    if any(not os.environ.get(key) for key in required):
        parser.error("database credentials must be provided through environment secrets")
    sql = Path(__file__).resolve().parents[1] / "infra/azure/bootstrap-postgres.sql"
    subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--host",
            args.host,
            "--username",
            args.admin_user,
            "--dbname",
            "postgres",
            "--file",
            str(sql),
        ],
        check=True,
        env={**os.environ, "PGSSLMODE": "verify-full"},
        timeout=300,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
