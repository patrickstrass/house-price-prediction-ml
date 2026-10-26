"""Transactional publication of training projections."""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import mlflow.sklearn

from house_price.data.validation import DatasetSnapshot
from house_price.domain import CandidateFamily, ModelCandidate, TrainingStatus
from house_price.registry.atomic import sha256_file
from house_price.registry.pointer import read_pointer
from house_price.registry.promotion import result_identity
from house_price.settings import Settings
from house_price.tracking.observability import ObservabilityRepository


class ProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackageBuildResult:
    path: Path
    model_version: str


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_model_package(
    *,
    root: Path,
    winner: ModelCandidate,
    estimator: object,
    plots: list[Path],
    snapshot: DatasetSnapshot,
    settings: Settings,
    split_seed: int,
    test_fraction: float,
) -> PackageBuildResult:
    """Build a self-contained v2 package before any catalog mutation."""
    build_root = root / ".package-build"
    build_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="model-", dir=build_root))
    try:
        model_dir = temporary / "model"
        # This model is fitted from the validated local snapshot. Tree estimators contain
        # sklearn's internal Tree object, which skops requires us to trust explicitly.
        trusted_types = (
            ["sklearn.tree._tree.Tree"]
            if winner.family in {CandidateFamily.DECISION_TREE, CandidateFamily.RANDOM_FOREST}
            else None
        )
        mlflow.sklearn.save_model(
            estimator, path=str(model_dir), skops_trusted_types=trusted_types
        )
        serialized = next(
            (
                path
                for path in (
                    model_dir / "model.skops",
                    model_dir / "model.pkl",
                    model_dir / "model.joblib",
                )
                if path.is_file()
            ),
            None,
        )
        if serialized is None:
            raise ProjectionError("MLflow package does not contain a serialized model")
        model_checksum = sha256_file(serialized)
        parameters = _json_value(winner.parameters)
        metrics = _json_value(winner.metrics.__dict__)
        (temporary / "parameters.json").write_text(
            json.dumps(parameters, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "metrics.json").write_text(
            json.dumps(metrics, sort_keys=True) + "\n", encoding="utf-8"
        )
        lock_source = Path("/app/requirements.lock")
        if not lock_source.is_file():
            lock_source = Path("requirements.lock")
        shutil.copy2(lock_source, temporary / "requirements.lock")
        plot_paths: list[str] = []
        if plots:
            (temporary / "plots").mkdir()
        for plot in plots:
            destination = temporary / "plots" / plot.name
            shutil.copy2(plot, destination)
            plot_paths.append(f"plots/{plot.name}")

        model_version = result_identity(
            code_revision=settings.code_revision,
            dataset_checksum=snapshot.sha256,
            split_seed=split_seed,
            test_fraction=test_fraction,
            family=winner.family.value,
            parameters=parameters,
            model_checksum=model_checksum,
        )[:24]
        destination = build_root / model_version
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        previous = read_pointer(root)
        checksums = {
            path.relative_to(destination).as_posix(): sha256_file(path)
            for path in sorted(destination.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": "2.0.0",
            "model_version": model_version,
            "registered_model_name": settings.mlflow_registered_model_name,
            "training_run_id": winner.training_run_id,
            "source_mlflow_run_id": winner.mlflow_run_id,
            "source_mlflow_model_version": None,
            "candidate_family": winner.family.value,
            "code_revision": settings.code_revision,
            "training_environment_version": settings.training_environment_version,
            "dataset": {
                "name": snapshot.dataset_name,
                "version": snapshot.dataset_version,
                "sha256": snapshot.sha256,
                "row_count": snapshot.row_count,
                "schema_version": snapshot.schema_version,
            },
            "parameters": parameters,
            "metrics": metrics,
            "signature": {
                "inputs": [{"name": name, "type": "double"} for name in snapshot.feature_names],
                "output": "double",
            },
            "artifacts": {
                "model_format": "mlflow",
                "model_path": "model",
                "parameters_path": "parameters.json",
                "metrics_path": "metrics.json",
                "dependency_lock_path": "requirements.lock",
                "plots": plot_paths,
                "checksums": checksums,
            },
            "promoted_at": datetime.now(UTC).isoformat(),
            "previous_model_version": previous.model_version if previous else None,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return PackageBuildResult(destination, model_version)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


class TrainingPublisher:
    def __init__(self, repository: ObservabilityRepository) -> None:
        self.repository = repository

    def publish_run(self, **values: object) -> None:
        try:
            self.repository.upsert_training_run(values)
        except Exception as exc:
            raise ProjectionError("failed to publish training run") from exc

    def publish_candidate(self, candidate: ModelCandidate) -> None:
        values = {
            "training_run_id": candidate.training_run_id,
            "candidate_family": candidate.family.value,
            "mlflow_run_id": candidate.mlflow_run_id,
            "best_trial_number": candidate.best_trial_number,
            **candidate.metrics.__dict__,
            "eligible": candidate.eligible,
            "promoted": candidate.promoted,
            "ineligibility_reasons": json.dumps(candidate.ineligibility_reasons),
            "measured_at": candidate.measured_at or datetime.now(UTC),
            "artifact_uri": candidate.artifact_uri,
        }
        try:
            self.repository.upsert_model_metric(values)
        except Exception as exc:
            raise ProjectionError("failed to publish model candidate") from exc

    def publish_trial(
        self,
        *,
        training_run_id: str,
        candidate_family: str,
        study_name: str,
        trial: Any,
        mlflow_run_id: str,
    ) -> None:
        state_name = trial.state.name.lower()
        if state_name == "fail":
            state_name = "failed"
        duration = getattr(trial, "duration", None)
        values = {
            "study_name": study_name,
            "trial_number": trial.number,
            "training_run_id": training_run_id,
            "candidate_family": candidate_family,
            "mlflow_run_id": mlflow_run_id,
            "objective_rmse": getattr(trial, "value", None),
            "state": state_name,
            "duration_seconds": duration.total_seconds() if duration else None,
            "parameters_json": json.dumps(trial.params, sort_keys=True),
            "completed_at": getattr(trial, "datetime_complete", None),
        }
        try:
            self.repository.upsert_optimization_trial(values)
        except Exception as exc:
            raise ProjectionError("failed to publish optimization trial") from exc

    def publish_plot(
        self,
        *,
        training_run_id: str,
        candidate_family: str,
        mlflow_run_id: str,
        plot: Path,
    ) -> None:
        plot_type = plot.stem
        identity = f"{training_run_id}:{candidate_family}:{plot_type}"
        values = {
            "plot_id": str(uuid5(NAMESPACE_URL, identity)),
            "training_run_id": training_run_id,
            "mlflow_run_id": mlflow_run_id,
            "candidate_family": candidate_family,
            "plot_type": plot_type,
            "artifact_uri": f"runs:/{mlflow_run_id}/plots/{plot.name}",
            "created_at": datetime.now(UTC),
        }
        try:
            self.repository.upsert_artifact_plot(values)
        except Exception as exc:
            raise ProjectionError("failed to publish artifact plot") from exc

    def publish_failure(self, run_values: dict[str, object], stage: str) -> None:
        self.publish_run(
            **{
                **run_values,
                "status": TrainingStatus.FAILED.value,
                "failure_stage": stage,
                "completed_at": datetime.now(UTC),
            }
        )
