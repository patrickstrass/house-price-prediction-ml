"""Correlated backup manifest from quiesced dumps and share snapshots."""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from house_price.azure.evidence import now_utc, sha256_file, validate, write_json

DATABASES = ("house_price", "mlflow", "optuna")


def _artifact(path: Path) -> dict:
    return {"uri": path.as_uri(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _archive_directory(source: Path, destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(source, arcname=".", recursive=True)


def capture(
    *,
    dumps_dir: Path,
    artifacts_dir: Path,
    registry_dir: Path,
    destination: Path,
    source_release_id: str | None,
    model_version: str | None,
    pointer_sha256: str | None,
    image_digests: list[str],
    retention_until: str,
    writers_quiesced: bool,
) -> dict:
    if not writers_quiesced:
        raise ValueError("writer quiescence evidence is required")
    backup_id = str(uuid4())
    target = destination / backup_id
    target.mkdir(parents=True, exist_ok=False)
    copied_dumps = {}
    for database in DATABASES:
        source = dumps_dir / f"{database}.dump"
        if not source.is_file():
            raise FileNotFoundError(source)
        output = target / source.name
        shutil.copy2(source, output)
        copied_dumps[database] = _artifact(output)
    artifacts_copy = target / "mlflow-artifacts.tar.gz"
    registry_copy = target / "model-registry.tar.gz"
    _archive_directory(artifacts_dir, artifacts_copy)
    _archive_directory(registry_dir, registry_copy)
    result = {
        "schema_version": "1.0.0",
        "backup_set_id": backup_id,
        "captured_at": now_utc(),
        "source_release_id": source_release_id,
        "database_dumps": copied_dumps,
        "artifact_copy": _artifact(artifacts_copy),
        "registry_copy": _artifact(registry_copy),
        "model_version": model_version,
        "pointer_sha256": pointer_sha256,
        "image_digests": image_digests,
        "write_quiesced": True,
        "verified_at": None,
        "restore_test": "not_run",
        "retention_until": retention_until,
        "status": "capturing",
    }
    if not verify(result):
        raise ValueError("captured backup failed checksum verification")
    result["verified_at"] = now_utc()
    result["status"] = "complete"
    validate(result, "azure-backup-set.schema.json")
    write_json(target / "backup-set.json", result, "azure-backup-set.schema.json")
    return result


def verify(manifest: dict) -> bool:
    validate(manifest, "azure-backup-set.schema.json")
    artifacts = [
        *manifest["database_dumps"].values(),
        manifest["artifact_copy"],
        manifest["registry_copy"],
    ]
    return all(
        _local_file(item["uri"]).is_file()
        and _local_file(item["uri"]).stat().st_size == item["size_bytes"]
        and sha256_file(_local_file(item["uri"])) == item["sha256"]
        for item in artifacts
    )


def _local_file(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("backup verification requires a local file URI")
    return Path(unquote(parsed.path))


def stage_restore(manifest: dict, isolated_target: Path) -> Path:
    """Verify and stage a backup; a DBA performs DB restore and catalog reconciliation later."""
    if not verify(manifest):
        raise ValueError("backup checksum mismatch")
    if isolated_target.exists() and any(isolated_target.iterdir()):
        raise ValueError("restore target must be empty and isolated")
    isolated_target.mkdir(parents=True, exist_ok=True)
    dumps = isolated_target / "dumps"
    dumps.mkdir()
    for database, artifact in manifest["database_dumps"].items():
        shutil.copy2(_local_file(artifact["uri"]), dumps / f"{database}.dump")
    for key, directory in (
        ("artifact_copy", "mlflow-artifacts"),
        ("registry_copy", "model-registry"),
    ):
        destination = isolated_target / directory
        destination.mkdir()
        with tarfile.open(_local_file(manifest[key]["uri"]), "r:gz") as archive:
            archive.extractall(destination, filter="data")
    return isolated_target


def restore_isolated(
    manifest: dict,
    *,
    staging: Path,
    artifact_target: Path,
    registry_target: Path,
    release: dict,
    restore_database: Callable[[str, Path], None],
    reconcile_and_verify: Callable[[Path], None],
    health_and_prediction: Callable[[dict], None],
) -> dict:
    """Restore a correlated set into isolated stores and record every verified phase."""
    validate(release, "azure-release.schema.json")
    if manifest["status"] != "complete" or release["status"] != "verified":
        raise ValueError("restore requires a complete backup and verified release")
    if manifest["source_release_id"] != release["release_id"]:
        raise ValueError("backup and release are not correlated")
    if manifest["model_version"] != release["model_version"]:
        raise ValueError("backup champion differs from release")
    for target in (artifact_target, registry_target):
        if target.resolve().is_relative_to(staging.resolve()):
            raise ValueError("restore stores cannot be inside the staging directory")
        if target.exists() and any(target.iterdir()):
            raise ValueError("restore stores must be empty and isolated")
    if len({staging.resolve(), artifact_target.resolve(), registry_target.resolve()}) != 3:
        raise ValueError("restore stores must be distinct")
    stage_restore(manifest, staging)
    for database in DATABASES:
        restore_database(database, staging / "dumps" / f"{database}.dump")
    shutil.copytree(staging / "mlflow-artifacts", artifact_target, dirs_exist_ok=True)
    shutil.copytree(staging / "model-registry", registry_target, dirs_exist_ok=True)
    if manifest["pointer_sha256"] and (
        sha256_file(registry_target / "current.json") != manifest["pointer_sha256"]
    ):
        raise ValueError("restored champion pointer checksum differs from backup")
    reconcile_and_verify(registry_target)
    health_and_prediction(release)
    result = {**manifest, "restore_test": "passed", "status": "restored"}
    validate(result, "azure-backup-set.schema.json")
    write_json(staging / "restore-result.json", result, "azure-backup-set.schema.json")
    return result
