"""Command-line contract for the batch trainer."""

import argparse
import json
import sys
from uuid import UUID, uuid4

from mlflow.exceptions import MlflowException
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from house_price.data.validation import DatasetValidationError
from house_price.domain import CandidateFamily
from house_price.logging import configure_logging, log_context
from house_price.registry.promotion import ConcurrentPromotionError, PromotionError
from house_price.settings import Settings
from house_price.training.evaluation import select_winner
from house_price.training.pipeline import PipelineOptions, run_pipeline
from house_price.training.publishing import ProjectionError


def _uuid(value: str) -> str:
    return str(UUID(value))


def _trial_budget(value: str) -> tuple[CandidateFamily, int]:
    try:
        family, count = value.split("=", 1)
        parsed = (CandidateFamily(family), int(count))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("expected FAMILY=COUNT") from exc
    if parsed[1] < 1:
        raise argparse.ArgumentTypeError("trial count must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="train")
    result.add_argument("--run-id", type=_uuid, default=None)
    result.add_argument("--seed", type=int, default=42)
    result.add_argument("--test-fraction", type=float, default=0.33)
    result.add_argument("--cv-folds", type=int, default=5)
    result.add_argument("--timeout-seconds", type=int, default=1_700)
    result.add_argument("--trial-budget", action="append", type=_trial_budget, default=[])
    result.add_argument("--resume", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if not 0 < args.test_fraction < 1 or args.cv_folds < 2 or args.timeout_seconds < 1:
            raise ValueError("invalid split, folds, or timeout")
        settings = Settings()  # type: ignore[call-arg]
    except (ValueError, ValidationError) as exc:
        print(json.dumps({"event": "configuration_invalid", "reason_code": type(exc).__name__}))
        return 2
    configure_logging(settings.log_level)
    run_id = args.run_id or str(uuid4())
    budgets = dict(args.trial_budget) or None
    options = PipelineOptions(
        run_id=run_id,
        seed=args.seed,
        test_fraction=args.test_fraction,
        cv_folds=args.cv_folds,
        timeout_seconds=args.timeout_seconds,
        trial_budgets=budgets,
        resume=args.resume,
    )
    try:
        with log_context(
            training_run_id=run_id,
            environment_version=settings.training_environment_version,
            stage="starting",
        ):
            result = run_pipeline(settings, options)
        winner = select_winner(list(result.candidates))
        decision = "promoted" if result.promoted else "unchanged" if winner else "rejected"
        print(
            json.dumps(
                {
                    "event": "training_completed",
                    "training_run_id": result.run_id,
                    "status": result.status.value,
                    "promoted": result.promoted,
                    "model_version": result.model_version,
                    "duration_seconds": result.duration_seconds,
                    "dataset_identity": result.dataset_identity,
                    "previous_model_version": result.previous_model_version,
                    "effective_model_version": (
                        result.model_version or result.previous_model_version
                    ),
                    "promotion_decision": decision,
                    "mlflow_parent_run_id": result.mlflow_parent_run_id,
                    "winner_family": winner.family.value if winner else None,
                    "candidate_metrics": [
                        {
                            "family": item.family.value,
                            "mlflow_run_id": item.mlflow_run_id,
                            "metrics": item.metrics.__dict__,
                            "parameters": item.parameters,
                            "artifact_uri": item.artifact_uri,
                            "eligible": item.eligible,
                        }
                        for item in result.candidates
                    ],
                },
                sort_keys=True,
                default=str,
            )
        )
        return 0
    except DatasetValidationError as exc:
        print(json.dumps({"event": "dataset_invalid", "reason_code": type(exc).__name__}))
        return 3
    except (ConnectionError, TimeoutError, OperationalError, MlflowException) as exc:
        print(json.dumps({"event": "dependency_unavailable", "reason_code": type(exc).__name__}))
        return 4
    except ProjectionError as exc:
        print(json.dumps({"event": "publication_failed", "reason_code": type(exc).__name__}))
        return 6
    except PromotionError as exc:
        print(json.dumps({"event": "promotion_failed", "reason_code": type(exc).__name__}))
        return 7
    except ConcurrentPromotionError:
        print(json.dumps({"event": "skipped_concurrent", "training_run_id": run_id}))
        return 8
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"event": "training_failed", "reason_code": type(exc).__name__}))
        return 5


if __name__ == "__main__":
    sys.exit(main())
