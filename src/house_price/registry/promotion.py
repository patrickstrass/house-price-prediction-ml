"""Durable publication saga coordinating ledger, MLflow, package, alias, and pointer."""

import hashlib
import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from house_price.catalog.models import (
    CatalogStatus,
    IntegrityStatus,
    LifecycleStatus,
    ModelEntry,
    OperationEvent,
    PromotionStatus,
)
from house_price.catalog.repository import CatalogRepository
from house_price.registry.atomic import move_immutable, sha256_file
from house_price.registry.manifest import ChampionManifest, validate_package
from house_price.registry.pointer import ChampionPointer, read_pointer, write_pointer
from house_price.tracking.mlflow_client import TrackingClient

PROMOTION_LOCK_ID = 0x485052494345


def result_identity(
    *,
    code_revision: str,
    dataset_checksum: str,
    split_seed: int,
    test_fraction: float,
    family: str,
    parameters: dict[str, Any],
    model_checksum: str,
) -> str:
    canonical = json.dumps(
        {
            "code_revision": code_revision,
            "dataset_checksum": dataset_checksum,
            "split_seed": split_seed,
            "test_fraction": test_fraction,
            "family": family,
            "parameters": parameters,
            "model_checksum": model_checksum,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ConcurrentPromotionError(RuntimeError):
    pass


class PromotionError(RuntimeError):
    """The candidate package could not be validated or atomically promoted."""


class PromotionManager:
    def __init__(
        self,
        root: Path,
        tracking: TrackingClient,
        *,
        lock_engine: Engine | None = None,
        repository: CatalogRepository | None = None,
    ) -> None:
        self.root = root
        self.tracking = tracking
        self.lock_engine = lock_engine
        self.repository = repository

    @contextmanager
    def lock(self) -> Iterator[Any]:
        if self.repository is not None:
            with self.repository.mutation_lock() as connection:
                yield connection
            return
        if self.lock_engine is None:
            yield None
            return
        with self.lock_engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": PROMOTION_LOCK_ID}
            ).scalar_one()
            if not acquired:
                raise ConcurrentPromotionError("another promotion is in progress")
            try:
                yield connection
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": PROMOTION_LOCK_ID}
                )

    def current_pointer(self) -> dict[str, Any] | None:
        pointer = read_pointer(self.root)
        return pointer.model_dump(mode="json") if pointer else None

    def promote(self, staging: Path, *, mlflow_version: str) -> tuple[str, bool]:
        manifest = validate_package(staging)
        with self.lock():
            return self.promote_locked(staging, mlflow_version=mlflow_version, manifest=manifest)

    def promote_locked(
        self,
        staging: Path,
        *,
        mlflow_version: str,
        manifest: ChampionManifest | None = None,
    ) -> tuple[str, bool]:
        manifest = manifest or validate_package(staging)
        model_version = manifest.model_version
        current = read_pointer(self.root)
        if current and current.model_version == model_version:
            return model_version, False
        models = self.root / "models"
        models.mkdir(parents=True, exist_ok=True)
        destination = models / model_version
        if destination.exists():
            existing = validate_package(destination)
            if existing.model_checksum != manifest.model_checksum:
                raise PromotionError("immutable package identity has conflicting content")
        else:
            move_immutable(staging, destination)
        previous_alias = self.tracking.get_champion_version()
        self.tracking.set_champion(mlflow_version)
        pointer = ChampionPointer(
            model_version=model_version,
            manifest_path=f"models/{model_version}/manifest.json",
            manifest_sha256=sha256_file(destination / "manifest.json"),
        )
        try:
            write_pointer(self.root / "current.json", pointer)
        except BaseException:
            self.tracking.restore_champion(previous_alias)
            raise
        return model_version, True


class PublicationSaga:
    """Resumable publication with `current.json` as the serving commit point."""

    def __init__(
        self,
        root: Path,
        tracking: TrackingClient,
        repository: CatalogRepository,
        *,
        phase_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.tracking = tracking
        self.repository = repository
        self.phase_hook = phase_hook

    def _phase(self, name: str) -> None:
        if self.phase_hook:
            self.phase_hook(name)

    @staticmethod
    def _event(
        operation_id,
        model_version: str,
        phase: str,
        result: str,
        reason_code: str | None = None,
    ) -> OperationEvent:
        return OperationEvent(
            event_id=uuid4(),
            operation_id=operation_id,
            operation="publish",
            model_version=model_version,
            phase=phase,
            result=result,
            reason_code=reason_code,
            occurred_at=datetime.now(UTC),
        )

    def _entry(self, manifest: ChampionManifest, manifest_sha256: str) -> ModelEntry:
        return ModelEntry(
            model_version=manifest.model_version,
            manifest_schema_version=manifest.schema_version,
            manifest_sha256=manifest_sha256,
            model_sha256=manifest.model_checksum,
            package_relpath=f"models/{manifest.model_version}",
            training_run_id=manifest.training_run_id,
            source_mlflow_run_id=manifest.mlflow_run_id,
            source_mlflow_model_version=manifest.mlflow_model_version,
            registered_model_name="house-price-regressor",
            integrity_status=IntegrityStatus.VERIFIED,
            catalog_status=CatalogStatus.REGISTERING,
            lifecycle_status=LifecycleStatus.ACTIVE,
            promotion_status=PromotionStatus.CANDIDATE,
            first_seen_at=datetime.now(UTC),
            verified_at=datetime.now(UTC),
        )

    def _upload_metadata(self, package: Path, run_id: str) -> None:
        for name in ("manifest.json", "parameters.json", "metrics.json", "requirements.lock"):
            path = package / name
            if path.is_file():
                target = "metadata" if name != "requirements.lock" else "metadata"
                self.tracking.log_artifact_to_run(run_id, path, target)

    def publish(self, package: Path, *, model_uri: str) -> tuple[str, bool]:
        manifest = validate_package(package)
        operation_id = uuid4()
        manifest_sha256 = sha256_file(package / "manifest.json")
        entry = self._entry(manifest, manifest_sha256)
        previous_alias: str | None = None
        alias_changed = False
        pointer_committed = False
        connection = None
        try:
            with self.repository.mutation_lock() as connection:
                current = read_pointer(self.root)
                if current and current.model_version == manifest.model_version:
                    shutil.rmtree(package, ignore_errors=True)
                    return manifest.model_version, False
                self.repository.upsert_model_entry(entry, connection=connection)
                self.repository.append_event(
                    self._event(operation_id, manifest.model_version, "intent", "started"),
                    connection=connection,
                )
                self._phase("intent_persisted")

                self._upload_metadata(package, manifest.mlflow_run_id)
                self._phase("artifacts_uploaded")
                tags = {
                    "catalog.schema_version": manifest.schema_version,
                    "catalog.model_version": manifest.model_version,
                    "catalog.manifest_sha256": manifest_sha256,
                    "catalog.model_sha256": manifest.model_checksum,
                    "catalog.training_run_id": str(manifest.training_run_id),
                    "catalog.dataset_sha256": manifest.dataset.sha256,
                    "catalog.origin": "training",
                    "catalog.integrity": "verified",
                    "catalog.publication_state": "preparing",
                }
                self.tracking.set_run_tags(manifest.mlflow_run_id, tags)
                mlflow_version = self.tracking.register_model_version(
                    model_uri=model_uri,
                    run_id=manifest.mlflow_run_id,
                    model_version=manifest.model_version,
                    tags=tags,
                )
                self._phase("model_version_created")
                entry = self.repository.update_model_entry(
                    manifest.model_version,
                    entry.revision,
                    {
                        "active_mlflow_run_id": manifest.mlflow_run_id,
                        "active_mlflow_model_version": mlflow_version,
                        "catalog_status": CatalogStatus.REGISTERED,
                    },
                    connection=connection,
                )

                destination = self.root / "models" / manifest.model_version
                if destination.exists():
                    existing = validate_package(destination)
                    if existing.model_checksum != manifest.model_checksum:
                        raise PromotionError("immutable package content conflict")
                    shutil.rmtree(package, ignore_errors=True)
                else:
                    move_immutable(package, destination)
                self._phase("package_materialized")

                previous_alias = self.tracking.get_champion_version()
                self.tracking.set_champion(mlflow_version)
                alias_changed = True
                self._phase("alias_prepared")
                write_pointer(
                    self.root / "current.json",
                    ChampionPointer(
                        model_version=manifest.model_version,
                        manifest_path=f"models/{manifest.model_version}/manifest.json",
                        manifest_sha256=manifest_sha256,
                    ),
                )
                pointer_committed = True
                self._phase("pointer_committed")
                self.tracking.set_model_version_tags(
                    mlflow_version, {"catalog.publication_state": "approved"}
                )
                self.tracking.set_run_tags(
                    manifest.mlflow_run_id,
                    {"catalog.publication_state": "approved"},
                )
                for previous in self.repository.list_model_entries(connection=connection):
                    if (
                        previous.model_version != manifest.model_version
                        and previous.promotion_status is PromotionStatus.CHAMPION
                    ):
                        self.repository.update_model_entry(
                            previous.model_version,
                            previous.revision,
                            {"promotion_status": PromotionStatus.SUPERSEDED},
                            connection=connection,
                        )
                self.repository.update_model_entry(
                    manifest.model_version,
                    entry.revision,
                    {"promotion_status": PromotionStatus.CHAMPION},
                    connection=connection,
                )
                self.repository.append_event(
                    self._event(operation_id, manifest.model_version, "approved", "succeeded"),
                    connection=connection,
                )
                self._phase("approved")
                return manifest.model_version, True
        except BaseException as exc:
            if alias_changed and not pointer_committed:
                self.tracking.restore_champion(previous_alias)
            reason = (
                "publication_interrupted"
                if isinstance(exc, KeyboardInterrupt)
                else "publication_failed"
            )
            try:
                failed = self.repository.get_model_entry(manifest.model_version)
                if failed is None:
                    failed = entry.model_copy(
                        update={
                            "catalog_status": CatalogStatus.REGISTRATION_FAILED,
                            "diagnostic_code": reason,
                        }
                    )
                    self.repository.upsert_model_entry(failed)
                self.repository.append_event(
                    self._event(
                        operation_id,
                        manifest.model_version,
                        "commit" if pointer_committed else "compensation",
                        "compensated" if alias_changed and not pointer_committed else "failed",
                        reason,
                    )
                )
            except BaseException:
                pass
            raise


def prepare_staging(source: Path, root: Path, model_version: str) -> Path:
    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    destination = staging_root / model_version
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    validate_package(destination)
    return destination
