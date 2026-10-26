"""Guarded release records for a future Container Apps deployment."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from house_price.azure.evidence import now_utc, read_json, validate
from house_price.azure.storage_probe import configuration_sha256


def rollback_champion(
    *, root: Path, previous_release: dict[str, Any], repository: Any, tracking: Any
) -> str:
    """Restore the prior verified package, alias and pointer under the catalog lock."""
    from house_price.catalog.models import PromotionStatus
    from house_price.registry.atomic import sha256_file
    from house_price.registry.manifest import validate_package
    from house_price.registry.pointer import ChampionPointer, read_pointer, write_pointer

    if previous_release["status"] != "verified":
        raise ValueError("rollback target was not verified")
    target = previous_release["model_version"]
    package = root / "models" / target
    manifest = validate_package(package, require_directory_identity=True)
    if manifest.dataset.sha256 != previous_release["dataset_sha256"]:
        raise ValueError("rollback package dataset differs from the release")
    if previous_release.get("mlflow_run_id") and (
        manifest.mlflow_run_id != previous_release["mlflow_run_id"]
    ):
        raise ValueError("rollback package MLflow run differs from the release")
    pointer = ChampionPointer(
        model_version=target,
        manifest_path=f"models/{target}/manifest.json",
        manifest_sha256=sha256_file(package / "manifest.json"),
    )
    old_pointer = read_pointer(root)
    old_alias = tracking.get_champion_version()
    changed_alias = False
    changed_pointer = False
    try:
        with repository.mutation_lock() as connection:
            entry = repository.get_model_entry(target, connection=connection)
            if entry is None or not entry.promotable or not entry.active_mlflow_model_version:
                raise ValueError("rollback package has no verified catalog entry")
            version = tracking.find_model_version(target)
            if version is None or str(version.version) != entry.active_mlflow_model_version:
                raise ValueError("rollback MLflow version differs from the catalog")
            for item in repository.list_model_entries(connection=connection):
                if (
                    item.promotion_status is PromotionStatus.CHAMPION
                    and item.model_version != target
                ):
                    repository.update_model_entry(
                        item.model_version, item.revision,
                        {"promotion_status": PromotionStatus.SUPERSEDED},
                        connection=connection,
                    )
            if entry.promotion_status is not PromotionStatus.CHAMPION:
                repository.update_model_entry(
                    target, entry.revision,
                    {"promotion_status": PromotionStatus.CHAMPION},
                    connection=connection,
                )
            tracking.set_champion(entry.active_mlflow_model_version)
            changed_alias = True
            write_pointer(root / "current.json", pointer)
            changed_pointer = True
    except BaseException:
        if changed_alias:
            tracking.restore_champion(old_alias)
        if changed_pointer and old_pointer is not None:
            write_pointer(root / "current.json", old_pointer)
        raise
    return target


def _fresh(timestamp: str, *, hours: int = 24) -> bool:
    checked = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return checked.tzinfo is not None and datetime.now(UTC) - timedelta(
        hours=hours
    ) <= checked <= datetime.now(UTC)


def check_gates(
    preflight: dict[str, Any], storage: dict[str, Any], manifest: dict[str, Any],
    *, expected_storage_config: dict[str, Any] | None = None,
) -> None:
    validate(preflight, "azure-preflight.schema.json")
    if preflight["status"] != "passed" or preflight["feature_002_gate"] != "passed":
        raise ValueError("preflight or feature 002 gate failed")
    if not _fresh(preflight["checked_at"]):
        raise ValueError("preflight is stale")
    if (
        storage.get("status") != "passed"
        or storage.get("preflight_id") != preflight["preflight_id"]
    ):
        raise ValueError("real ACA storage probe is required")
    required_checks = {
        "owner_uid",
        "artifact_owner_uid",
        "distinct_stores",
        "writable",
        "same_filesystem",
        "atomic_replace",
        "directory_fsync",
        "immutable_collision",
        "concurrent_readers",
        "injected_failure",
        "readonly_inference",
        "five_recreations",
        "aca_provenance",
    }
    if not all(storage.get("checks", {}).get(key) is True for key in required_checks):
        raise ValueError("storage probe lacks mandatory evidence")
    if expected_storage_config is not None:
        fingerprint = configuration_sha256(expected_storage_config)
        if (
            storage.get("configuration") != expected_storage_config
            or storage.get("configuration_sha256") != fingerprint
        ):
            raise ValueError("storage probe configuration changed; a new ACA probe is required")
    images = manifest.get("images", {})
    if set(images) != {"trainer", "inference", "mlflow"}:
        raise ValueError("manifest needs all three images")
    for item in images.values():
        if not item.get("ci_approved") or item["tag"].endswith(":latest"):
            raise ValueError("only uniquely tagged CI-approved images are deployable")


def prepare_release(
    *,
    preflight_path: Path,
    storage_path: Path,
    manifest_path: Path,
    target_config_path: Path,
) -> dict[str, Any]:
    preflight = read_json(preflight_path)
    storage = read_json(storage_path)
    manifest = read_json(manifest_path)
    target = read_json(target_config_path)
    if "storage_config" not in target:
        raise ValueError("target needs the tested storage configuration")
    check_gates(preflight, storage, manifest, expected_storage_config=target["storage_config"])
    if target["subscription_id"] != preflight["subscription_id"]:
        raise ValueError("target subscription mismatch")
    config_bytes = json.dumps(target["config"], sort_keys=True, separators=(",", ":")).encode()
    record = {
        "schema_version": "1.0.0",
        "release_id": str(uuid4()),
        "preflight_id": preflight["preflight_id"],
        "storage_probe_id": storage["probe_id"],
        "git_revision": manifest["git_revision"],
        "ci_run_id": str(manifest["ci_run_id"]),
        "environment_id": storage["environment_id"],
        "images": manifest["images"],
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "model_version": target["model_version"],
        "dataset_sha256": target["dataset_sha256"],
        "mlflow_run_id": target.get("mlflow_run_id"),
        "inference_revision": "pending",
        "previous_release_id": target.get("previous_release_id"),
        "backup_set_id": target.get("backup_set_id"),
        "health_result": "not_run",
        "prediction_result": "not_run",
        "rollback_result": "not_run",
        "status": "prepared",
        "created_at": now_utc(),
        "completed_at": None,
    }
    validate(record, "azure-release.schema.json")
    return record


CANONICAL_PREDICTION = {
    "instances": [
        {
            "MedInc": 8.3252,
            "HouseAge": 41,
            "AveRooms": 6.9841,
            "AveBedrms": 1.0238,
            "Population": 322,
            "AveOccup": 2.5556,
            "Latitude": 37.88,
            "Longitude": -122.23,
        }
    ]
}


def verify_http(base_url: str, record: dict[str, Any]) -> dict[str, Any]:
    if not base_url.startswith("https://"):
        raise ValueError("release verification requires HTTPS")
    origin = base_url.rstrip("/")

    def request(path: str, body: dict | None = None) -> dict:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        action = urllib.request.Request(origin + path, data=payload, headers=headers)
        with urllib.request.urlopen(action, timeout=15) as response:  # noqa: S310
            return json.load(response)

    ready = request("/health/ready")
    model = request("/v1/model")
    prediction = request("/v1/predictions", CANONICAL_PREDICTION)
    expected = record["model_version"]
    if any(item.get("model_version") != expected for item in (ready, model, prediction)):
        raise ValueError("served champion does not match release")
    if model.get("dataset_checksum") != record["dataset_sha256"]:
        raise ValueError("served dataset does not match release")
    if record.get("mlflow_run_id") and model.get("mlflow_run_id") != record["mlflow_run_id"]:
        raise ValueError("served MLflow run does not match release")
    if not prediction.get("predictions"):
        raise ValueError("prediction response is empty")
    result = dict(record)
    result.update(
        {
            "health_result": "passed",
            "prediction_result": "passed",
            "status": "verified",
            "completed_at": now_utc(),
        }
    )
    validate(result, "azure-release.schema.json")
    return result


def check_catalog_evidence(record: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Require a successful catalog check for the same champion before traffic moves."""
    if evidence.get("status") != "succeeded":
        raise ValueError("catalog verification did not succeed")
    if not evidence.get("timestamp") or not _fresh(evidence["timestamp"], hours=1):
        raise ValueError("catalog verification is missing or stale")
    items = evidence.get("items") or []
    matches = [item for item in items if item.get("model_version") == record["model_version"]]
    if len(matches) != 1 or matches[0].get("status") != "verified":
        raise ValueError("catalog does not verify the served champion")
