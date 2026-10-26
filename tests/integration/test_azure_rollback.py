"""Local simulation of the private ACA champion rollback job."""

import shutil
from pathlib import Path
from types import MethodType

from house_price.azure.release import rollback_champion
from house_price.catalog.models import PromotionStatus
from house_price.catalog.reconciliation import CatalogReconciler
from house_price.registry.manifest import validate_package
from house_price.registry.pointer import read_pointer
from tests.integration.test_catalog_reconciliation import MemoryRepository, MemoryTracking


def test_rollback_reactivates_previous_package_alias_and_ledger(
    project_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "registry"
    shutil.copytree(project_root / "tests/fixtures/model-registry", root)
    repository = MemoryRepository()
    tracking = MemoryTracking()
    service = CatalogReconciler(
        root, repository=repository, tracking=tracking, minimum_free_bytes=1
    )
    assert service.reconcile(mode="apply", trigger="restore")["counts"]["registered"] == 1
    target = repository.entries["fixture-v1"]
    old = target.model_copy(update={
        "model_version": "newer-v2", "promotion_status": PromotionStatus.CHAMPION,
    })
    repository.entries[old.model_version] = old
    tracking.current = "newer-alias"

    def set_champion(self, version):
        self.current = version

    tracking.set_champion = MethodType(set_champion, tracking)
    tracking.restore_champion = MethodType(set_champion, tracking)
    manifest = validate_package(root / "models/fixture-v1")
    previous = {
        "status": "verified", "model_version": "fixture-v1",
        "dataset_sha256": manifest.dataset.sha256,
        "mlflow_run_id": manifest.mlflow_run_id,
    }
    assert rollback_champion(
        root=root, previous_release=previous, repository=repository, tracking=tracking
    ) == "fixture-v1"
    assert read_pointer(root).model_version == "fixture-v1"
    assert tracking.current == target.active_mlflow_model_version
    assert repository.entries["fixture-v1"].promotion_status is PromotionStatus.CHAMPION
    assert repository.entries["newer-v2"].promotion_status is PromotionStatus.SUPERSEDED
