"""End-to-end deterministic training, tracking, evaluation, and promotion."""

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import mlflow
from sqlalchemy import create_engine

from house_price.catalog.reconciliation import CatalogReconciler
from house_price.catalog.repository import CatalogRepository
from house_price.data.validation import DatasetSnapshot, load_and_validate_snapshot
from house_price.domain import CandidateFamily, ModelCandidate, ModelMetrics, TrainingStatus
from house_price.registry.pointer import read_pointer
from house_price.registry.promotion import (
    ConcurrentPromotionError,
    PromotionError,
    PublicationSaga,
)
from house_price.settings import Settings
from house_price.tracking.mlflow_client import TrackingClient
from house_price.tracking.observability import ObservabilityRepository
from house_price.training.dataset import build_estimator, deterministic_split, unwrap_estimator
from house_price.training.evaluation import apply_gate, regression_metrics, select_winner
from house_price.training.optimization import optimize_family
from house_price.training.plots import evaluation_plots, optimization_history, parameter_importance
from house_price.training.publishing import TrainingPublisher, build_model_package
from house_price.training.search_spaces import DEFAULT_TRIAL_BUDGETS
from house_price.training.tracking import TrainingTracker

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineOptions:
    run_id: str
    seed: int = 42
    test_fraction: float = 0.33
    cv_folds: int = 5
    timeout_seconds: int = 1_700
    trial_budgets: dict[CandidateFamily, int] | None = None
    resume: bool = False


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    status: TrainingStatus
    candidates: tuple[ModelCandidate, ...]
    promoted: bool
    model_version: str | None
    duration_seconds: float
    dataset_identity: dict[str, str] | None = None
    previous_model_version: str | None = None
    mlflow_parent_run_id: str | None = None


def _champion_metrics(registry_root: Path) -> ModelMetrics | None:
    pointer_path = registry_root / "current.json"
    if not pointer_path.is_file():
        return None
    pointer = json.loads(pointer_path.read_text())
    manifest = json.loads((registry_root / pointer["manifest_path"]).read_text())
    return ModelMetrics(**manifest["metrics"])


def _run_values(
    options: PipelineOptions,
    snapshot: DatasetSnapshot,
    parent_run_id: str,
    code_revision: str,
    started_at: datetime,
) -> dict[str, object]:
    return {
        "training_run_id": options.run_id,
        "mlflow_parent_run_id": parent_run_id,
        "dataset_version": snapshot.dataset_version,
        "dataset_checksum": snapshot.sha256,
        "code_revision": code_revision,
        "started_at": started_at,
        "completed_at": None,
        "status": TrainingStatus.PENDING.value,
        "failure_stage": None,
        "promoted_model_version": None,
    }


def run_pipeline(settings: Settings, options: PipelineOptions) -> PipelineResult:
    started_clock = monotonic()
    started_at = datetime.now(UTC)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    catalog_repository = CatalogRepository(
        engine=engine, advisory_lock_id=settings.catalog_advisory_lock_id
    )
    tracking_client = TrackingClient(
        settings.mlflow_tracking_uri,
        settings.mlflow_experiment_name,
        recovery_experiment_name=settings.mlflow_recovery_experiment_name,
        registered_model_name=settings.mlflow_registered_model_name,
        champion_alias=settings.mlflow_champion_alias,
    )
    CatalogReconciler(
        settings.model_registry_path,
        repository=catalog_repository,
        tracking=tracking_client,
    ).preflight(probe_artifacts=True)
    frame, snapshot = load_and_validate_snapshot(
        settings.dataset_path, settings.dataset_manifest_path
    )
    split = deterministic_split(frame, seed=options.seed, test_fraction=options.test_fraction)
    tracker = TrainingTracker(tracking_client)
    publisher = TrainingPublisher(ObservabilityRepository(engine=engine))
    champion_metrics = _champion_metrics(settings.model_registry_path)
    previous_pointer = read_pointer(settings.model_registry_path)
    previous_model_version = previous_pointer.model_version if previous_pointer else None
    dataset_identity = {"version": snapshot.dataset_version, "sha256": snapshot.sha256}
    candidates: list[ModelCandidate] = []
    fitted: dict[CandidateFamily, object] = {}
    candidate_plots: dict[CandidateFamily, list[Path]] = {}
    parent_tags = {
        "training_run_id": options.run_id,
        "code_revision": settings.code_revision,
        "dataset_version": snapshot.dataset_version,
        "dataset_checksum": snapshot.sha256,
    }
    run_values: dict[str, object] | None = None
    stage = TrainingStatus.VALIDATING.value

    try:
        with tracker.parent_run(options.run_id, parent_tags) as parent_run_id:
            run_values = _run_values(
                options, snapshot, parent_run_id, settings.code_revision, started_at
            )
            publisher.publish_run(**run_values)
            stage = TrainingStatus.VALIDATING.value
            publisher.publish_run(**{**run_values, "status": stage})
            stage = TrainingStatus.OPTIMIZING.value
            publisher.publish_run(**{**run_values, "status": stage})
            budgets = {**DEFAULT_TRIAL_BUDGETS, **(options.trial_budgets or {})}
            per_family_timeout = max(1, options.timeout_seconds // len(CandidateFamily))
            with tempfile.TemporaryDirectory(prefix="house-price-plots-") as plot_root:
                for family in CandidateFamily:
                    family_tags = {**parent_tags, "candidate_family": family.value}
                    with tracker.family_run(family.value, family_tags) as family_run_id:
                        optimization = optimize_family(
                            options.run_id,
                            family,
                            split.x_train,
                            split.y_train,
                            storage_url=settings.optuna_storage_url,
                            trial_budget=budgets[family],
                            seed=options.seed,
                            folds=options.cv_folds,
                            timeout=per_family_timeout,
                        )
                        for trial in optimization.study.trials:
                            if trial.value is None:
                                continue
                            with tracker.trial_run(
                                family.value, trial.number, trial.params
                            ) as trial_run_id:
                                mlflow.log_metric("cv_rmse_mean", float(trial.value))
                            publisher.publish_trial(
                                training_run_id=options.run_id,
                                candidate_family=family.value,
                                study_name=optimization.study_name,
                                trial=trial,
                                mlflow_run_id=trial_run_id,
                            )
                        estimator = build_estimator(
                            family, optimization.parameters, seed=options.seed
                        )
                        estimator.fit(split.x_train, split.y_train)
                        prediction = estimator.predict(split.x_test)
                        metrics = regression_metrics(split.y_test, prediction)
                        family_plot_dir = Path(plot_root) / family.value
                        plots = evaluation_plots(
                            split.y_test.to_numpy(),
                            prediction,
                            family_plot_dir,
                            feature_names=snapshot.feature_names,
                            estimator=unwrap_estimator(estimator),
                        )
                        plots.append(optimization_history(optimization.study, family_plot_dir))
                        plots.append(parameter_importance(optimization.study, family_plot_dir))
                        tracker.log_finalist(
                            estimator,
                            metrics,
                            optimization.parameters,
                            plots,
                            input_example=split.x_train.head(3),
                        )
                        candidate = apply_gate(
                            ModelCandidate(
                                training_run_id=options.run_id,
                                family=family,
                                mlflow_run_id=family_run_id,
                                best_trial_number=optimization.best_trial_number,
                                parameters=optimization.parameters,
                                metrics=metrics,
                                artifact_uri=f"runs:/{family_run_id}/model",
                                measured_at=datetime.now(UTC),
                            ),
                            champion_metrics,
                        )
                        candidates.append(candidate)
                        fitted[family] = estimator
                        candidate_plots[family] = plots
                        for plot in plots:
                            publisher.publish_plot(
                                training_run_id=options.run_id,
                                candidate_family=family.value,
                                mlflow_run_id=family_run_id,
                                plot=plot,
                            )

                stage = TrainingStatus.EVALUATING.value
                publisher.publish_run(**{**run_values, "status": stage})
                stage = TrainingStatus.PUBLISHING_METRICS.value
                publisher.publish_run(**{**run_values, "status": stage})
                for candidate in candidates:
                    publisher.publish_candidate(candidate)
                winner = select_winner(candidates)
                if winner is None:
                    current_pointer = read_pointer(settings.model_registry_path)
                    tracker.record_selection(
                        parent_run_id=parent_run_id,
                        candidates=candidates,
                        winner=None,
                        decision="rejected",
                        effective_model_version=current_pointer.model_version
                        if current_pointer else None,
                    )
                    completed = datetime.now(UTC)
                    publisher.publish_run(
                        **{
                            **run_values,
                            "status": TrainingStatus.SUCCEEDED_NO_PROMOTION.value,
                            "completed_at": completed,
                        }
                    )
                    return PipelineResult(
                        options.run_id,
                        TrainingStatus.SUCCEEDED_NO_PROMOTION,
                        tuple(candidates),
                        False,
                        None,
                        monotonic() - started_clock,
                        dataset_identity,
                        previous_model_version,
                        parent_run_id,
                    )

                stage = TrainingStatus.PROMOTING.value
                publisher.publish_run(**{**run_values, "status": stage})
                package = build_model_package(
                    root=settings.model_registry_path,
                    winner=winner,
                    estimator=fitted[winner.family],
                    plots=candidate_plots[winner.family],
                    snapshot=snapshot,
                    settings=settings,
                    split_seed=options.seed,
                    test_fraction=options.test_fraction,
                )
                try:
                    model_version, promoted = PublicationSaga(
                        settings.model_registry_path,
                        tracking_client,
                        catalog_repository,
                    ).publish(package.path, model_uri=winner.artifact_uri)
                except Exception as exc:
                    current_pointer = read_pointer(settings.model_registry_path)
                    tracker.record_selection(
                        parent_run_id=parent_run_id,
                        candidates=candidates,
                        winner=winner,
                        decision="publication_failed",
                        effective_model_version=current_pointer.model_version
                        if current_pointer else None,
                    )
                    raise PromotionError("candidate publication saga failed") from exc
                winner.promoted = promoted
                tracker.record_selection(
                    parent_run_id=parent_run_id,
                    candidates=candidates,
                    winner=winner,
                    decision="promoted" if promoted else "unchanged",
                    effective_model_version=model_version,
                )
                publisher.publish_candidate(winner)
                status = (
                    TrainingStatus.SUCCEEDED if promoted else TrainingStatus.SUCCEEDED_NO_PROMOTION
                )
                publisher.publish_run(
                    **{
                        **run_values,
                        "status": status.value,
                        "completed_at": datetime.now(UTC),
                        "promoted_model_version": model_version if promoted else None,
                    }
                )
                return PipelineResult(
                    options.run_id,
                    status,
                    tuple(candidates),
                    promoted,
                    model_version,
                    monotonic() - started_clock,
                    dataset_identity,
                    previous_model_version,
                    parent_run_id,
                )
    except ConcurrentPromotionError:
        LOGGER.warning("concurrent promotion skipped", extra={"stage": stage})
        if run_values is not None:
            publisher.publish_run(
                **{
                    **run_values,
                    "status": TrainingStatus.SKIPPED_CONCURRENT.value,
                    "completed_at": datetime.now(UTC),
                }
            )
        raise
    except Exception:
        LOGGER.exception("training pipeline failed", extra={"stage": stage})
        if run_values is not None:
            try:
                publisher.publish_failure(run_values, stage)
            except Exception:
                LOGGER.exception("failed to publish pipeline failure", extra={"stage": stage})
        raise


def new_options() -> PipelineOptions:
    return PipelineOptions(run_id=str(uuid4()))
