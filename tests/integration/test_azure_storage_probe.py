import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from house_price.azure.storage_probe import finalize_probe, probe_mount, probe_readonly
from house_price.registry.promotion import PromotionManager


def test_local_probe_is_explicit_about_uid_and_atomic_ops(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    artifacts = tmp_path / "artifacts"
    registry.mkdir()
    artifacts.mkdir()
    result = probe_mount(
        registry,
        artifacts,
        expected_uid=registry.stat().st_uid,
        artifact_uid=artifacts.stat().st_uid,
    )
    assert result["status"] == "passed"
    assert result["checks"]["atomic_replace"]
    assert result["checks"]["directory_fsync"]


def test_uid_mismatch_fails(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    artifacts = tmp_path / "artifacts"
    registry.mkdir()
    artifacts.mkdir()
    assert (
        probe_mount(registry, artifacts, expected_uid=-1, artifact_uid=artifacts.stat().st_uid)[
            "status"
        ]
        == "failed"
    )


def test_finalize_requires_readonly_and_five_distinct_recreations(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    artifacts = tmp_path / "artifacts"
    registry.mkdir()
    artifacts.mkdir()
    basic = probe_mount(
        registry,
        artifacts,
        expected_uid=registry.stat().st_uid,
        artifact_uid=artifacts.stat().st_uid,
    )
    basic["source"] = "aca"
    observations = [
        {
            "recreation_id": str(i),
            "model_version": "v1",
            "pointer_sha256": "a" * 64,
            "source": "aca",
        }
        for i in range(5)
    ]
    good = finalize_probe(
        basic, {"status": "passed", "readonly_inference": True, "source": "aca"}, observations
    )
    assert good["status"] == "passed"
    observations[4]["model_version"] = "v2"
    assert (
        finalize_probe(
            basic, {"status": "passed", "readonly_inference": True, "source": "aca"}, observations
        )["status"]
        == "failed"
    )


def test_probe_detects_failed_replace_and_preserves_existing_file(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    artifacts = tmp_path / "artifacts"
    registry.mkdir()
    artifacts.mkdir()
    with patch("house_price.azure.storage_probe.os.replace", side_effect=OSError("mount failure")):
        result = probe_mount(
            registry, artifacts,
            expected_uid=registry.stat().st_uid,
            artifact_uid=artifacts.stat().st_uid,
        )
    assert result["status"] == "failed"
    assert result["checks"]["filesystem_ops"] is False
    assert not list(registry.iterdir())


def test_probe_readonly_requires_actual_write_denial(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    registry.mkdir()
    result = probe_readonly(registry, expected_uid=registry.stat().st_uid)
    assert result["status"] == "failed"  # Writable local directory cannot prove ACA RO.
    assert result["readonly_inference"] is False


def test_five_recreations_require_distinct_replicas(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    artifacts = tmp_path / "artifacts"
    registry.mkdir()
    artifacts.mkdir()
    basic = probe_mount(
        registry, artifacts,
        expected_uid=registry.stat().st_uid,
        artifact_uid=artifacts.stat().st_uid,
    )
    basic["source"] = "aca"
    observations = [
        {"recreation_id": "same", "model_version": "v1",
         "pointer_sha256": "a" * 64, "source": "aca"}
        for _ in range(5)
    ]
    result = finalize_probe(
        basic, {"status": "passed", "readonly_inference": True, "source": "aca"},
        observations,
    )
    assert result["status"] == "failed"
    assert result["checks"]["five_recreations"] is False


def test_failure_between_alias_and_pointer_restores_alias(
    project_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    package = tmp_path / "staging"
    shutil.copytree(project_root / "tests/fixtures/model-registry/models/fixture-v1", package)
    alias = {"version": "old"}
    tracking = SimpleNamespace(
        get_champion_version=lambda: alias["version"],
        set_champion=lambda version: alias.update(version=version),
        restore_champion=lambda version: alias.update(version=version),
    )
    with patch("house_price.registry.promotion.write_pointer", side_effect=OSError("fault")):
        with pytest.raises(OSError, match="fault"):
            PromotionManager(root, tracking).promote(package, mlflow_version="new")
    assert alias["version"] == "old"
    assert not (root / "current.json").exists()
