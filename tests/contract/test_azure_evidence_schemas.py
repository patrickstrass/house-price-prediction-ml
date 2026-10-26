import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from house_price.azure.evidence import SCHEMAS, read_json, validate, write_json


@pytest.mark.parametrize(
    "name",
    ["azure-preflight.schema.json", "azure-release.schema.json", "azure-backup-set.schema.json"],
)
def test_schema_is_valid(name: str) -> None:
    Draft202012Validator.check_schema(json.loads((SCHEMAS / name).read_text()))


def test_atomic_evidence_is_private(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    write_json(output, {"schema_version": "1.0.0"})
    assert read_json(output) == {"schema_version": "1.0.0"}
    assert output.stat().st_mode & 0o077 == 0


def test_invalid_document_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        write_json(tmp_path / "invalid.json", {"status": "passed"}, "azure-preflight.schema.json")


def test_release_rejects_invalid_identity_digest_state_and_missing_fields() -> None:
    release = {
        "schema_version": "1.0.0",
        "release_id": "00000000-0000-4000-8000-000000000001",
        "preflight_id": "00000000-0000-4000-8000-000000000002",
        "storage_probe_id": "00000000-0000-4000-8000-000000000003",
        "git_revision": "abcdef12345",
        "ci_run_id": "123",
        "environment_id": "aca-env",
        "images": {
            name: {"tag": f"registry/{name}:sha-123", "digest": "sha256:" + "a" * 64,
                   "ci_approved": True}
            for name in ("trainer", "inference", "mlflow")
        },
        "config_sha256": "b" * 64,
        "model_version": "v1",
        "dataset_sha256": "c" * 64,
        "inference_revision": "pending",
        "health_result": "not_run",
        "prediction_result": "not_run",
        "rollback_result": "not_run",
        "status": "prepared",
        "created_at": "2026-10-25T00:00:00Z",
        "completed_at": None,
    }
    validate(release, "azure-release.schema.json")
    for path, bad in (
        (("release_id",), "bad-id"),
        (("images", "trainer", "digest"), "sha256:bad"),
        (("status",), "done"),
    ):
        changed = json.loads(json.dumps(release))
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad
        with pytest.raises(ValidationError):
            validate(changed, "azure-release.schema.json")
    del release["dataset_sha256"]
    with pytest.raises(ValidationError):
        validate(release, "azure-release.schema.json")
