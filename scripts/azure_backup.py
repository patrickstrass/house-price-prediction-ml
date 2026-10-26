"""Prepare and verify a correlated backup from locally mounted stores."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

from house_price.azure.backup import capture, restore_isolated, stage_restore, verify
from house_price.azure.evidence import read_json
from house_price.azure.release import verify_http


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("capture")
    take.add_argument("--dumps-dir", type=Path, required=True)
    take.add_argument("--artifacts-dir", type=Path, required=True)
    take.add_argument("--registry-dir", type=Path, required=True)
    take.add_argument("--destination", type=Path, required=True)
    take.add_argument("--retention-until", required=True)
    take.add_argument("--writers-quiesced", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("--backup-set", type=Path, required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--backup-set", type=Path, required=True)
    stage.add_argument("--isolated-target", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup-set", type=Path, required=True)
    restore.add_argument("--isolated-target", type=Path, required=True)
    restore.add_argument("--artifact-target", type=Path, required=True)
    restore.add_argument("--registry-target", type=Path, required=True)
    restore.add_argument("--release", type=Path, required=True)
    restore.add_argument("--health-url", required=True)
    restore.add_argument("--target-environment-id", required=True)
    restore.add_argument("--confirm-isolated", action="store_true")
    args = parser.parse_args()
    if args.command == "capture":
        manifest = capture(
            dumps_dir=args.dumps_dir,
            artifacts_dir=args.artifacts_dir,
            registry_dir=args.registry_dir,
            destination=args.destination,
            source_release_id=None,
            model_version=None,
            pointer_sha256=None,
            image_digests=[],
            retention_until=args.retention_until,
            writers_quiesced=args.writers_quiesced,
        )
        print(json.dumps(manifest, sort_keys=True))
        return 0
    if args.command == "stage":
        result = stage_restore(read_json(args.backup_set), args.isolated_target)
        print(json.dumps({"staged_at": str(result), "database_restore": "pending"}))
        return 0
    if args.command == "restore":
        release = read_json(args.release)
        if not args.confirm_isolated or args.target_environment_id == release["environment_id"]:
            raise ValueError("restore target must be a separate, confirmed environment")
        target_urls = {
            database: os.environ[f"RESTORE_{database.upper()}_DATABASE_URL"]
            for database in ("house_price", "mlflow", "optuna")
        }
        active_urls = {
            "house_price": os.environ.get("MIGRATION_DATABASE_URL"),
            "mlflow": os.environ.get("MLFLOW_BACKEND_STORE_URI"),
            "optuna": os.environ.get("OPTUNA_STORAGE_URL"),
        }
        for database, url in target_urls.items():
            target_url = make_url(url)
            if not target_url.password:
                raise ValueError("restore database credentials must come from environment")
            if active_urls[database]:
                active_url = make_url(active_urls[database])
                if (target_url.host, target_url.port, target_url.database) == (
                    active_url.host, active_url.port, active_url.database
                ):
                    raise ValueError("restore database matches an active database")
        tracking_uri = os.environ["RESTORE_MLFLOW_TRACKING_URI"]
        if not tracking_uri.startswith("https://"):
            raise ValueError("isolated tracking endpoint requires HTTPS")

        def restore_database(database: str, dump: Path) -> None:
            url = make_url(target_urls[database])
            safe = url.set(drivername="postgresql", password=None).render_as_string(
                hide_password=False
            )
            env = {**os.environ, "PGPASSWORD": url.password}
            subprocess.run(
                ["pg_restore", "--no-owner", "--no-privileges", "--dbname", safe, str(dump)],
                check=True, timeout=1200, env=env,
            )

        def reconcile(registry: Path) -> None:
            env = {
                **os.environ, "DATABASE_URL": target_urls["house_price"],
                "MIGRATION_DATABASE_URL": target_urls["house_price"],
                "OPTUNA_STORAGE_URL": target_urls["optuna"],
                "MLFLOW_TRACKING_URI": tracking_uri,
                "MODEL_REGISTRY_PATH": str(registry),
            }
            for command in (
                ["model-catalog", "reconcile", "--mode", "apply", "--trigger", "restore", "--json"],
                ["model-catalog", "verify", "--probe-artifacts", "--json"],
            ):
                subprocess.run(command, check=True, timeout=1200, env=env)

        result = restore_isolated(
            read_json(args.backup_set), staging=args.isolated_target,
            artifact_target=args.artifact_target, registry_target=args.registry_target,
            release=release, restore_database=restore_database,
            reconcile_and_verify=reconcile,
            health_and_prediction=lambda record: verify_http(args.health_url, record),
        )
        print(json.dumps({
            "backup_set_id": result["backup_set_id"], "status": result["status"],
            "restore_test": result["restore_test"],
            "target_environment_id": args.target_environment_id,
        }))
        return 0
    good = verify(read_json(args.backup_set))
    print(json.dumps({"verified": good}))
    return 0 if good else 5


if __name__ == "__main__":
    raise SystemExit(main())
