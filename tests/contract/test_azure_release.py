import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import ValidationError

from house_price.azure.evidence import validate
from house_price.azure.preflight import REQUIRED_PROVIDERS, REQUIRED_RESOURCES, build_report
from house_price.azure.release import (
    check_catalog_evidence,
    check_gates,
    prepare_release,
    verify_http,
)
from house_price.azure.storage_probe import configuration_sha256
from scripts import azure_release


def test_release_rejects_missing_aca_probe() -> None:
    with pytest.raises(ValidationError):
        check_gates({"status": "passed"}, {"status": "pending"}, {})


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    subscription_id = str(uuid4())
    timestamp = datetime.now(UTC).isoformat()
    preflight = build_report(
        subscription={"id": subscription_id, "offer_type": "Azure for Students"},
        region="brazilsouth",
        credit={
            "subscription_id": subscription_id,
            "remaining": 100,
            "currency": "USD",
            "source": "portal",
            "checked_at": timestamp,
        },
        prices={
            "currency": "USD", "region": "brazilsouth",
            "checked_at": timestamp,
            "resources": [
                {
                    "resource_type": name,
                    "sku": "example",
                    "monthly_estimate": 1,
                    "source": "calculator",
                }
                for name in REQUIRED_RESOURCES
            ],
        },
        providers=[
            {"namespace": name, "registration_state": "Registered", "regional_availability": True}
            for name in REQUIRED_PROVIDERS
        ],
        quotas=[{"name": "cores", "required": 1, "available": 2, "unit": "count"}],
        operational_limit=Decimal(50),
        safety_margin=Decimal(5),
        feature_002_gate="passed",
    )
    checks = {
        name: True
        for name in (
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
        )
    }
    storage = {
        "probe_id": str(uuid4()),
        "preflight_id": preflight["preflight_id"],
        "environment_id": "/subscriptions/test/aca",
        "status": "passed",
        "checks": checks,
    }
    storage_config = {
        "protocol": "SMB", "registry_mount": "/model-registry",
        "artifact_mount": "/mlflow-artifacts", "uid_rw": 10001,
        "uid_ro": 10001, "runtime": "sha256:" + "c" * 64,
    }
    storage["configuration"] = storage_config
    storage["configuration_sha256"] = configuration_sha256(storage_config)
    image = {
        "tag": "example.azurecr.io/inference:sha-123",
        "digest": "sha256:" + "a" * 64,
        "ci_approved": True,
    }
    manifest = {
        "git_revision": "abcdef123456",
        "ci_run_id": "123",
        "images": {name: image for name in ("trainer", "inference", "mlflow")},
    }
    target = {
        "subscription_id": subscription_id,
        "config": {"replicas": 1},
        "storage_config": storage_config,
        "model_version": "v1",
        "dataset_sha256": "b" * 64,
    }
    paths = tuple(
        tmp_path / name
        for name in ("preflight.json", "storage.json", "manifest.json", "target.json")
    )
    for path, value in zip(paths, (preflight, storage, manifest, target), strict=True):
        path.write_text(json.dumps(value))
    return paths


def test_prepare_release_has_immutable_links(tmp_path: Path) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight,
        storage_path=storage,
        manifest_path=manifest,
        target_config_path=target,
    )
    assert record["status"] == "prepared"
    assert record["images"]["inference"]["digest"] == "sha256:" + "a" * 64
    validate(record, "azure-release.schema.json")


def test_release_blocks_unproved_storage_and_mutable_tag(tmp_path: Path) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    evidence = json.loads(storage.read_text())
    evidence["checks"]["five_recreations"] = False
    storage.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="storage probe"):
        prepare_release(
            preflight_path=preflight,
            storage_path=storage,
            manifest_path=manifest,
            target_config_path=target,
        )
    evidence["checks"]["five_recreations"] = True
    storage.write_text(json.dumps(evidence))
    images = json.loads(manifest.read_text())
    images["images"]["inference"]["tag"] = "example.azurecr.io/inference:latest"
    manifest.write_text(json.dumps(images))
    with pytest.raises(ValueError, match="CI-approved"):
        prepare_release(
            preflight_path=preflight,
            storage_path=storage,
            manifest_path=manifest,
            target_config_path=target,
        )


def test_verify_http_requires_model_dataset_and_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight,
        storage_path=storage,
        manifest_path=manifest,
        target_config_path=target,
    )
    responses = [
        {"model_version": "v1"},
        {"model_version": "v1", "dataset_checksum": "b" * 64},
        {"model_version": "v1", "predictions": [123.0]},
    ]
    monkeypatch.setattr(
        "house_price.azure.release.urllib.request.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps(responses.pop(0)).encode()),
    )
    result = verify_http("https://example.azurecontainerapps.io", record)
    assert result["status"] == "verified"
    assert result["prediction_result"] == "passed"


def test_deployed_revision_must_use_approved_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        azure_release, "az_json",
        lambda *_args: {"properties": {"template": {"containers": [{"image": "other:tag"}]}}},
    )
    with pytest.raises(ValueError, match="approved tag"):
        azure_release._check_revision_image("inference", "rg", "revision", "approved:tag")


def test_catalog_evidence_must_verify_the_release_champion(tmp_path: Path) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=manifest, target_config_path=target,
    )
    evidence = {
        "status": "succeeded", "timestamp": datetime.now(UTC).isoformat(),
        "items": [{"model_version": "v1", "status": "verified"}],
    }
    check_catalog_evidence(record, evidence)
    evidence["items"][0]["model_version"] = "other"
    with pytest.raises(ValueError, match="served champion"):
        check_catalog_evidence(record, evidence)
    evidence["status"] = "partial"
    with pytest.raises(ValueError, match="did not succeed"):
        check_catalog_evidence(record, evidence)


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_id", "not-a-uuid"),
        ("config_sha256", "short"),
        ("dataset_sha256", "not-a-checksum"),
        ("status", "unknown"),
    ],
)
def test_release_schema_rejects_invalid_core_fields(
    tmp_path: Path, field: str, value: str
) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=manifest, target_config_path=target,
    )
    record[field] = value
    with pytest.raises(ValidationError):
        validate(record, "azure-release.schema.json")


def test_verified_requires_health_and_prediction(tmp_path: Path) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=manifest, target_config_path=target,
    )
    record["status"] = "verified"
    with pytest.raises(ValidationError):
        validate(record, "azure-release.schema.json")


def test_release_rejects_invalid_digest_and_ci_gate(tmp_path: Path) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    images = json.loads(manifest.read_text())
    images["images"]["inference"]["digest"] = "sha256:bad"
    manifest.write_text(json.dumps(images))
    with pytest.raises(ValidationError):
        prepare_release(
            preflight_path=preflight, storage_path=storage,
            manifest_path=manifest, target_config_path=target,
        )
    images["images"]["inference"]["digest"] = "sha256:" + "a" * 64
    images["images"]["inference"]["ci_approved"] = False
    manifest.write_text(json.dumps(images))
    with pytest.raises(ValueError, match="CI-approved"):
        prepare_release(
            preflight_path=preflight, storage_path=storage,
            manifest_path=manifest, target_config_path=target,
        )


@pytest.mark.parametrize("changed", ["protocol", "registry_mount", "uid_ro", "runtime"])
def test_release_invalidates_changed_storage_configuration(tmp_path: Path, changed: str) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    data = json.loads(target.read_text())
    data["storage_config"][changed] = "changed"
    target.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="storage probe configuration changed"):
        prepare_release(
            preflight_path=preflight, storage_path=storage,
            manifest_path=manifest, target_config_path=target,
        )
