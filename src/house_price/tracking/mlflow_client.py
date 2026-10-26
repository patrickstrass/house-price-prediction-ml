"""Public-API-only MLflow boundary for training and catalog operations."""

import importlib.metadata
import tempfile
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

REGISTERED_MODEL_NAME = "house-price-regressor"
CHAMPION_ALIAS = "champion"
EXPERIMENT_NAME = "house-price-training-v2"
RECOVERY_EXPERIMENT_NAME = "house-price-model-reconciliation"
CATALOG_TAGS = {
    "catalog.schema_version",
    "catalog.model_version",
    "catalog.manifest_sha256",
    "catalog.model_sha256",
    "catalog.training_run_id",
    "catalog.dataset_sha256",
    "catalog.origin",
    "catalog.integrity",
    "catalog.publication_state",
}


class DuplicateModelIdentityError(RuntimeError):
    pass


class ModelIdentityConflictError(RuntimeError):
    pass


def _not_found(error: MlflowException) -> bool:
    return error.error_code in {"RESOURCE_DOES_NOT_EXIST", "ENDPOINT_NOT_FOUND"}


def _alias_not_found(error: MlflowException, alias: str) -> bool:
    return (
        error.error_code == "INVALID_PARAMETER_VALUE"
        and f"registered model alias {alias} not found" in str(error).lower()
    )


class TrackingClient:
    def __init__(
        self,
        tracking_uri: str,
        experiment_name: str = EXPERIMENT_NAME,
        *,
        recovery_experiment_name: str = RECOVERY_EXPERIMENT_NAME,
        registered_model_name: str = REGISTERED_MODEL_NAME,
        champion_alias: str = CHAMPION_ALIAS,
    ) -> None:
        self.tracking_uri = tracking_uri.rstrip("/")
        self.experiment_name = experiment_name
        self.recovery_experiment_name = recovery_experiment_name
        self.registered_model_name = registered_model_name
        self.champion_alias = champion_alias
        mlflow.set_tracking_uri(tracking_uri)
        self.client = MlflowClient(tracking_uri=tracking_uri)
        self._ensure_experiment(experiment_name, require_proxy=tracking_uri.startswith("http"))
        mlflow.set_experiment(experiment_name)

    def _ensure_experiment(self, name: str, *, require_proxy: bool) -> str:
        experiment = self.client.get_experiment_by_name(name)
        if experiment is None:
            artifact_location = "mlflow-artifacts:/" if require_proxy else None
            experiment_id = self.client.create_experiment(name, artifact_location=artifact_location)
            experiment = self.client.get_experiment(experiment_id)
        if require_proxy:
            self.validate_artifact_location(experiment.artifact_location)
        return str(experiment.experiment_id)

    def ensure_recovery_experiment(self) -> str:
        return self._ensure_experiment(self.recovery_experiment_name, require_proxy=True)

    def active_artifact_location(self) -> str:
        experiment = self.client.get_experiment_by_name(self.experiment_name)
        if experiment is None:
            raise RuntimeError("active MLflow experiment is missing")
        return self.validate_artifact_location(experiment.artifact_location)

    @staticmethod
    def validate_artifact_location(location: str) -> str:
        if not location.startswith("mlflow-artifacts:"):
            raise ValueError("new experiment artifact location must use mlflow-artifacts:")
        return location

    def assert_server_version(self) -> str:
        if not self.tracking_uri.startswith("http"):
            return importlib.metadata.version("mlflow")
        with urllib.request.urlopen(f"{self.tracking_uri}/version", timeout=5) as response:
            server_version = response.read().decode().strip().strip('"')
        client_version = importlib.metadata.version("mlflow")
        if server_version != client_version:
            raise RuntimeError(
                f"MLflow client/server mismatch: client={client_version} server={server_version}"
            )
        return server_version

    @contextmanager
    def run(
        self,
        *,
        run_name: str,
        tags: dict[str, str],
        nested: bool = False,
        experiment_id: str | None = None,
    ) -> Iterator[Any]:
        with mlflow.start_run(
            run_name=run_name,
            tags=tags,
            nested=nested,
            experiment_id=experiment_id,
        ) as active:
            yield active

    @staticmethod
    def log_parameters(parameters: dict[str, Any]) -> None:
        mlflow.log_params(parameters)

    @staticmethod
    def log_metrics(metrics: dict[str, float]) -> None:
        mlflow.log_metrics(metrics)

    @staticmethod
    def log_artifact(path: str, artifact_path: str | None = None) -> None:
        mlflow.log_artifact(path, artifact_path=artifact_path)

    @staticmethod
    def log_artifacts(path: str, artifact_path: str | None = None) -> None:
        mlflow.log_artifacts(path, artifact_path=artifact_path)

    def download_artifacts(self, run_id: str, path: str, destination: Path) -> Path:
        downloaded = self.client.download_artifacts(run_id, path, str(destination))
        return Path(downloaded)

    def log_artifact_to_run(
        self, run_id: str, path: Path, artifact_path: str | None = None
    ) -> None:
        self.client.log_artifact(run_id, str(path), artifact_path)

    def log_artifacts_to_run(
        self, run_id: str, path: Path, artifact_path: str | None = None
    ) -> None:
        self.client.log_artifacts(run_id, str(path), artifact_path)

    def set_run_tags(self, run_id: str, tags: Mapping[str, str]) -> None:
        for key, value in tags.items():
            self.client.set_tag(run_id, key, value)

    def set_model_version_tags(self, version: str, tags: Mapping[str, str]) -> None:
        for key, value in tags.items():
            self.client.set_model_version_tag(self.registered_model_name, version, key, value)

    def probe_artifacts(self) -> None:
        experiment_id = self.ensure_recovery_experiment()
        run = self.client.create_run(
            experiment_id,
            tags={"catalog.probe": "true", "catalog.origin": "recovery"},
        )
        try:
            with tempfile.TemporaryDirectory(prefix="catalog-artifact-probe-") as temporary:
                root = Path(temporary)
                source = root / "probe.txt"
                source.write_text("catalog-probe\n", encoding="utf-8")
                self.client.log_artifact(run.info.run_id, str(source), "diagnostics")
                destination = root / "download"
                downloaded = Path(
                    self.client.download_artifacts(
                        run.info.run_id, "diagnostics/probe.txt", str(destination)
                    )
                )
                if downloaded.read_bytes() != source.read_bytes():
                    raise RuntimeError("MLflow artifact proxy probe checksum mismatch")
        finally:
            self.client.delete_run(run.info.run_id)

    def ensure_registered_model(self) -> None:
        try:
            self.client.get_registered_model(self.registered_model_name)
        except MlflowException as exc:
            if not _not_found(exc):
                raise
            self.client.create_registered_model(self.registered_model_name)

    def find_model_versions(self, model_version: str) -> list[Any]:
        versions = self.client.search_model_versions(
            filter_string=f"name='{self.registered_model_name}'"
        )
        return [
            version
            for version in versions
            if (getattr(version, "tags", None) or {}).get("catalog.model_version") == model_version
        ]

    def find_model_version(self, model_version: str) -> Any | None:
        matches = self.find_model_versions(model_version)
        if len(matches) > 1:
            raise DuplicateModelIdentityError(
                f"multiple MLflow versions use catalog identity {model_version}"
            )
        return matches[0] if matches else None

    def register_model_version(
        self,
        *,
        model_uri: str,
        run_id: str,
        model_version: str,
        tags: Mapping[str, str],
    ) -> str:
        missing = CATALOG_TAGS.difference(tags)
        if missing:
            raise ValueError(f"missing catalog tags: {sorted(missing)}")
        existing = self.find_model_version(model_version)
        if existing is not None:
            for checksum in ("catalog.manifest_sha256", "catalog.model_sha256"):
                if existing.tags.get(checksum) != tags[checksum]:
                    raise ModelIdentityConflictError(
                        f"catalog identity {model_version} has conflicting {checksum}"
                    )
            return str(existing.version)
        self.ensure_registered_model()
        created = self.client.create_model_version(
            name=self.registered_model_name,
            source=model_uri,
            run_id=run_id,
            tags=dict(tags),
        )
        return str(created.version)

    def register_without_alias(self, model_uri: str, run_id: str) -> str:
        """Compatibility shim for legacy pipeline calls."""
        self.ensure_registered_model()
        version = mlflow.register_model(model_uri, self.registered_model_name)
        self.client.set_model_version_tag(
            self.registered_model_name, version.version, "source_run_id", run_id
        )
        return str(version.version)

    def get_run(self, run_id: str) -> Any | None:
        try:
            return self.client.get_run(run_id)
        except MlflowException as exc:
            if _not_found(exc):
                return None
            raise

    def get_champion_version(self) -> str | None:
        try:
            return str(
                self.client.get_model_version_by_alias(
                    self.registered_model_name, self.champion_alias
                ).version
            )
        except MlflowException as exc:
            if _not_found(exc) or _alias_not_found(exc, self.champion_alias):
                return None
            raise

    def set_champion(self, version: str) -> None:
        self.client.set_registered_model_alias(
            self.registered_model_name, self.champion_alias, version
        )

    def restore_champion(self, version: str | None) -> None:
        if version is None:
            try:
                self.client.delete_registered_model_alias(
                    self.registered_model_name, self.champion_alias
                )
            except MlflowException as exc:
                if not (_not_found(exc) or _alias_not_found(exc, self.champion_alias)):
                    raise
        else:
            self.set_champion(version)

    def delete_model_version(self, version: str) -> None:
        self.client.delete_model_version(self.registered_model_name, version)

    def recover_package(
        self,
        package: Path,
        manifest: Any,
        *,
        reconciliation_id: UUID,
        tags: Mapping[str, str],
    ) -> tuple[str, str]:
        experiment_id = self.ensure_recovery_experiment()
        recovery_tags = {
            **dict(tags),
            "catalog.origin": "recovery",
            "catalog.source_mlflow_run_id": manifest.source_mlflow_run_id,
            "catalog.reconciliation_id": str(reconciliation_id),
        }
        if manifest.source_mlflow_model_version:
            recovery_tags["catalog.source_mlflow_model_version"] = (
                manifest.source_mlflow_model_version
            )
        run = self.client.create_run(experiment_id, tags=recovery_tags)
        run_id = run.info.run_id
        try:
            self.client.log_batch(
                run_id,
                metrics=[
                    mlflow.entities.Metric(name, float(value), 0, 0)
                    for name, value in manifest.metrics.model_dump().items()
                ],
                params=[
                    mlflow.entities.Param(name, str(value))
                    for name, value in manifest.parameters.items()
                ],
                tags=[],
            )
            self.client.log_artifacts(run_id, str(package / "model"), "model")
            for name in ("manifest.json", "parameters.json", "metrics.json", "requirements.lock"):
                path = package / name
                if path.is_file():
                    self.client.log_artifact(run_id, str(path), "metadata")
            plots = package / "plots"
            if plots.is_dir():
                self.client.log_artifacts(run_id, str(plots), "plots")
            version = self.register_model_version(
                model_uri=f"runs:/{run_id}/model",
                run_id=run_id,
                model_version=manifest.model_version,
                tags=recovery_tags,
            )
            self.client.set_terminated(run_id, status="FINISHED")
            return run_id, version
        except BaseException:
            self.client.set_terminated(run_id, status="FAILED")
            raise
