"""ACA backup job: hold catalog mutation lock while capturing a backup set."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from house_price.azure.backup import capture
from house_price.azure.evidence import read_json, sha256_file, validate
from house_price.catalog.repository import CatalogRepository


def _pg_uri(url: str) -> tuple[str, dict[str, str]]:
    parsed = make_url(url)
    password = parsed.password
    if not password:
        raise ValueError("backup database URL must include a password from runtime secrets")
    safe = parsed.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return safe, {**os.environ, "PGPASSWORD": password}


def main() -> int:
    migration = os.environ["MIGRATION_DATABASE_URL"]
    optuna = os.environ["OPTUNA_STORAGE_URL"]
    mlflow = os.environ["MLFLOW_BACKEND_STORE_URI"]
    repository = CatalogRepository(database_url=migration)
    with repository.mutation_lock():
        pointer = Path("/model-registry/current.json")
        current_model = read_json(pointer).get("model_version") if pointer.exists() else None
        release_path = Path(os.environ.get("CURRENT_RELEASE_PATH", "/backup/current-release.json"))
        if os.environ.get("CURRENT_RELEASE_PATH") and not release_path.is_file():
            raise FileNotFoundError("current release record is required for weekly backup")
        release = read_json(release_path) if release_path.exists() else None
        if release:
            validate(release, "azure-release.schema.json")
            if release["model_version"] != current_model:
                raise ValueError("release and registry champion differ")
        with tempfile.TemporaryDirectory() as temp:
            dumps = Path(temp)
            for name, url in (("house_price", migration), ("mlflow", mlflow), ("optuna", optuna)):
                output = dumps / f"{name}.dump"
                safe_url, env = _pg_uri(url)
                subprocess.run(
                    ["pg_dump", "--format=custom", "--file", str(output), safe_url],
                    check=True,
                    timeout=1200,
                    env=env,
                )
            retention = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            manifest = capture(
                dumps_dir=dumps,
                artifacts_dir=Path("/mlflow-artifacts"),
                registry_dir=Path("/model-registry"),
                destination=Path("/backup"),
                source_release_id=release["release_id"] if release else None,
                model_version=current_model,
                pointer_sha256=sha256_file(pointer) if pointer.exists() else None,
                image_digests=[item["digest"] for item in release["images"].values()]
                if release
                else [],
                retention_until=retention,
                writers_quiesced=True,
            )
            print(json.dumps({
                "event": "backup_completed",
                "backup_set_id": manifest["backup_set_id"],
                "model_version": current_model,
                "source_release_id": manifest["source_release_id"],
            }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
