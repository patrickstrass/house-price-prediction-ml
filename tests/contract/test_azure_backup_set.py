import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import ValidationError

from house_price.azure.backup import capture, verify
from house_price.azure.evidence import validate


def test_capture_requires_quiescence_and_detects_corruption(tmp_path: Path) -> None:
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    for name in ("house_price", "mlflow", "optuna"):
        (dumps / f"{name}.dump").write_bytes(b"dump")
    artifacts = tmp_path / "artifacts"
    registry = tmp_path / "registry"
    artifacts.mkdir()
    registry.mkdir()
    options = dict(
        dumps_dir=dumps,
        artifacts_dir=artifacts,
        registry_dir=registry,
        destination=tmp_path / "backups",
        source_release_id=None,
        model_version=None,
        pointer_sha256=None,
        image_digests=[],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    with pytest.raises(ValueError):
        capture(**options, writers_quiesced=False)
    manifest = capture(**options, writers_quiesced=True)
    assert verify(manifest)
    Path(manifest["database_dumps"]["house_price"]["uri"].removeprefix("file://")).write_bytes(
        b"bad"
    )
    assert not verify(manifest)


def test_backup_schema_rejects_incomplete_or_unsafe_restored_set(tmp_path: Path) -> None:
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    for name in ("house_price", "mlflow", "optuna"):
        (dumps / f"{name}.dump").write_bytes(name.encode())
    artifacts = tmp_path / "artifacts"
    registry = tmp_path / "registry"
    artifacts.mkdir()
    registry.mkdir()
    manifest = capture(
        dumps_dir=dumps, artifacts_dir=artifacts, registry_dir=registry,
        destination=tmp_path / "backups", source_release_id=None,
        model_version=None, pointer_sha256=None, image_digests=[],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
        writers_quiesced=True,
    )
    for index, mutation in enumerate((
        lambda item: item["database_dumps"].pop("optuna"),
        lambda item: item.update(write_quiesced=False),
        lambda item: item.update(status="restored", restore_test="not_run"),
        lambda item: item["artifact_copy"].update(uri="https://user:secret@example/x"),
        lambda item: item["registry_copy"].update(sha256="invalid"),
        lambda item: item.update(retention_until="not-a-date"),
    )):
        changed = json.loads(json.dumps(manifest))
        mutation(changed)
        try:
            validate(changed, "azure-backup-set.schema.json")
        except ValidationError:
            pass
        else:
            pytest.fail(f"mutation {index} was accepted")
