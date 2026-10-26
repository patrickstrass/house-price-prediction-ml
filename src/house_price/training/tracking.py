"""MLflow hierarchy for pipeline, family, trial, and finalist artifacts."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import mlflow
import mlflow.sklearn

from house_price.domain import ModelCandidate, ModelMetrics
from house_price.tracking.mlflow_client import TrackingClient


class TrainingTracker:
    def __init__(self, client: TrackingClient) -> None:
        self.client = client

    def record_selection(
        self,
        *,
        parent_run_id: str,
        candidates: list[ModelCandidate],
        winner: ModelCandidate | None,
        decision: str,
        effective_model_version: str | None,
    ) -> None:
        """Make the cross-family decision visible on the parent and family runs."""
        winner_id = winner.mlflow_run_id if winner else None
        self.client.set_run_tags(parent_run_id, {
            "selection.decision": decision,
            "selection.winner_family": winner.family.value if winner else "none",
            "selection.winner_run_id": winner_id or "none",
            "selection.effective_model_version": effective_model_version or "none",
            "selection.metric": "holdout_rmse",
        })
        if winner is not None:
            mlflow.log_metric("selection.winner_holdout_rmse", winner.metrics.rmse)
        mlflow.log_metric("selection.family_count", len(candidates))
        for candidate in candidates:
            self.client.set_run_tags(candidate.mlflow_run_id, {
                "selection.eligible": str(candidate.eligible).lower(),
                "selection.winner": str(candidate.mlflow_run_id == winner_id).lower(),
                "selection.decision": (
                    decision if candidate.mlflow_run_id == winner_id else "not_selected"
                ),
            })
        mlflow.log_dict(
            {
                "schema_version": "1.0.0",
                "decision": decision,
                "winner_family": winner.family.value if winner else None,
                "winner_run_id": winner_id,
                "effective_model_version": effective_model_version,
                "candidates": [
                    {
                        "family": item.family.value,
                        "mlflow_run_id": item.mlflow_run_id,
                        "eligible": item.eligible,
                        "ineligibility_reasons": item.ineligibility_reasons,
                        "holdout_metrics": item.metrics.__dict__,
                        "parameters": item.parameters,
                        "winner": item.mlflow_run_id == winner_id,
                    }
                    for item in candidates
                ],
            },
            "selection/family-comparison.json",
        )

    @contextmanager
    def parent_run(self, run_id: str, tags: dict[str, str]) -> Iterator[str]:
        with self.client.run(run_name=f"training-{run_id}", tags=tags) as active:
            yield active.info.run_id

    @contextmanager
    def family_run(self, family: str, tags: dict[str, str]) -> Iterator[str]:
        with self.client.run(run_name=family, tags=tags, nested=True) as active:
            yield active.info.run_id

    @contextmanager
    def trial_run(self, family: str, number: int, parameters: dict[str, Any]) -> Iterator[str]:
        with self.client.run(
            run_name=f"{family}-trial-{number}",
            tags={"candidate_family": family, "trial_number": str(number)},
            nested=True,
        ) as active:
            mlflow.log_params(parameters)
            yield active.info.run_id

    @staticmethod
    def log_finalist(
        estimator: Any,
        metrics: ModelMetrics,
        parameters: dict[str, Any],
        plots: list[Path],
        *,
        input_example: Any,
    ) -> None:
        mlflow.log_params(parameters)
        mlflow.log_metrics(metrics.__dict__)
        for plot in plots:
            mlflow.log_artifact(str(plot), artifact_path="plots")
        with TemporaryDirectory(prefix="mlflow-model-") as temporary:
            model_path = Path(temporary) / "model"
            mlflow.sklearn.save_model(
                estimator,
                path=str(model_path),
                input_example=input_example,
                serialization_format="cloudpickle",
            )
            mlflow.log_artifacts(str(model_path), artifact_path="model")
