from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from house_price.azure.backup import capture, restore_isolated, stage_restore, verify
from house_price.azure.evidence import sha256_file
from house_price.azure.release import prepare_release
from house_price.catalog.reconciliation import CatalogReconciler
from house_price.registry.manifest import validate_package
from tests.contract.test_azure_release import _inputs
from tests.integration.test_catalog_reconciliation import MemoryRepository, MemoryTracking


def test_restore_isolated_stages_three_stores_without_mutating_source(tmp_path: Path) -> None:
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    for name in ("house_price", "mlflow", "optuna"):
        (dumps / f"{name}.dump").write_bytes(name.encode())
    artifacts = tmp_path / "artifacts"
    registry = tmp_path / "registry"
    artifacts.mkdir()
    registry.mkdir()
    (artifacts / "run.txt").write_text("run")
    (registry / "current.json").write_text("champion")
    manifest = capture(
        dumps_dir=dumps,
        artifacts_dir=artifacts,
        registry_dir=registry,
        destination=tmp_path / "backups",
        source_release_id=None,
        model_version="one",
        pointer_sha256=None,
        image_digests=[],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
        writers_quiesced=True,
    )
    isolated = stage_restore(manifest, tmp_path / "isolated")
    assert (isolated / "dumps/house_price.dump").read_bytes() == b"house_price"
    assert (isolated / "mlflow-artifacts/run.txt").read_text() == "run"
    assert (isolated / "model-registry/current.json").read_text() == "champion"
    with pytest.raises(ValueError):
        stage_restore(manifest, isolated)


def test_corrupt_dump_blocks_restore_before_target_is_created(tmp_path: Path) -> None:
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
        model_version="v1", pointer_sha256=None, image_digests=[],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
        writers_quiesced=True,
    )
    from urllib.parse import urlparse

    damaged = Path(urlparse(manifest["database_dumps"]["mlflow"]["uri"]).path)
    damaged.write_bytes(b"corrupted")
    assert verify(manifest) is False
    target = tmp_path / "isolated"
    with pytest.raises(ValueError, match="checksum"):
        stage_restore(manifest, target)
    assert not target.exists()


def test_isolated_reconcile_does_not_duplicate_or_promote(
    project_root: Path, tmp_path: Path
) -> None:
    import shutil

    dumps = tmp_path / "dumps"
    dumps.mkdir()
    for name in ("house_price", "mlflow", "optuna"):
        (dumps / f"{name}.dump").write_bytes(name.encode())
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    registry = tmp_path / "registry"
    source = project_root / "tests/fixtures/model-registry"
    shutil.copytree(source, registry)
    manifest = capture(
        dumps_dir=dumps, artifacts_dir=artifacts, registry_dir=registry,
        destination=tmp_path / "backups", source_release_id=None,
        model_version="fixture-v1", pointer_sha256=None, image_digests=[],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
        writers_quiesced=True,
    )
    isolated = stage_restore(manifest, tmp_path / "isolated")
    repository = MemoryRepository()
    tracking = MemoryTracking()
    service = CatalogReconciler(
        isolated / "model-registry", repository=repository, tracking=tracking,
        minimum_free_bytes=1,
    )
    first = service.reconcile(mode="apply", trigger="restore")
    second = service.reconcile(mode="apply", trigger="restore")
    assert first["counts"]["registered"] == 1
    assert second["counts"]["unchanged"] == 1
    assert tracking.version_creations == 1
    assert tracking.current is None
    (isolated / "model-registry/models/fixture-v1/model/model.joblib").write_bytes(b"corrupt")
    divergence = service.reconcile(mode="check", trigger="restore")
    assert divergence["counts"]["quarantined"] == 1
    assert tracking.version_creations == 1
    assert tracking.current is None


def test_full_isolated_restore_orders_databases_stores_and_verification(
    project_root: Path, tmp_path: Path
) -> None:
    preflight, storage, image_manifest, target = _inputs(tmp_path)
    release = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=image_manifest, target_config_path=target,
    )
    source = project_root / "tests/fixtures/model-registry"
    package = validate_package(source / "models/fixture-v1")
    release.update(
        status="verified", model_version="fixture-v1",
        dataset_sha256=package.dataset.sha256, mlflow_run_id=package.mlflow_run_id,
        health_result="passed", prediction_result="passed",
    )
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    for name in ("house_price", "mlflow", "optuna"):
        (dumps / f"{name}.dump").write_bytes(name.encode())
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    manifest = capture(
        dumps_dir=dumps, artifacts_dir=artifacts, registry_dir=source,
        destination=tmp_path / "backups", source_release_id=release["release_id"],
        model_version="fixture-v1", pointer_sha256=sha256_file(source / "current.json"),
        image_digests=["sha256:" + "a" * 64],
        retention_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
        writers_quiesced=True,
    )
    calls = []
    registry_target = tmp_path / "restored-registry"
    artifact_target = tmp_path / "restored-artifacts"

    def restore_database(name: str, dump: Path) -> None:
        assert dump.read_bytes() == name.encode()
        calls.append(name)

    def reconcile(registry: Path) -> None:
        assert registry == registry_target
        assert (registry / "current.json").is_file()
        assert artifact_target.is_dir()
        calls.append("reconcile_verify")

    result = restore_isolated(
        manifest, staging=tmp_path / "staging", artifact_target=artifact_target,
        registry_target=registry_target, release=release,
        restore_database=restore_database, reconcile_and_verify=reconcile,
        health_and_prediction=lambda _: calls.append("health_prediction"),
    )
    assert calls == ["house_price", "mlflow", "optuna", "reconcile_verify", "health_prediction"]
    assert result["status"] == "restored"
    assert (tmp_path / "staging/restore-result.json").is_file()
