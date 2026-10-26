import json
from argparse import ArgumentTypeError
from types import SimpleNamespace

import pytest

from house_price.data.validation import DatasetValidationError
from house_price.domain import CandidateFamily
from house_price.registry.promotion import ConcurrentPromotionError, PromotionError
from house_price.training import cli
from house_price.training.cli import _trial_budget, main, parser
from house_price.training.publishing import ProjectionError


def test_cli_defaults_and_repeatable_trial_budget() -> None:
    args = parser().parse_args(["--trial-budget", "ridge=3", "--trial-budget", "lasso=4"])
    assert args.seed == 42
    assert args.test_fraction == 0.33
    assert args.cv_folds == 5
    assert args.trial_budget == [(CandidateFamily.RIDGE, 3), (CandidateFamily.LASSO, 4)]


@pytest.mark.parametrize("value", ["unknown=1", "ridge=0", "broken"])
def test_trial_budget_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ArgumentTypeError):
        _trial_budget(value)


def test_invalid_arguments_return_code_2(capsys) -> None:
    assert main(["--test-fraction", "2"]) == 2
    assert json.loads(capsys.readouterr().out)["event"] == "configuration_invalid"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (DatasetValidationError("dataset"), 3),
        (ConnectionError("postgres"), 4),
        (RuntimeError("optimization"), 5),
        (ProjectionError("projection"), 6),
        (PromotionError("promotion"), 7),
        (ConcurrentPromotionError("concurrent"), 8),
        (KeyboardInterrupt(), 130),
    ],
)
def test_runtime_failures_map_to_contract_exit_codes(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException, expected: int
) -> None:
    settings = SimpleNamespace(log_level="ERROR", training_environment_version="test")
    monkeypatch.setattr(cli, "Settings", lambda: settings)

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(cli, "run_pipeline", fail)
    assert main([]) == expected


def test_success_returns_zero_and_json_line(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    settings = SimpleNamespace(log_level="ERROR", training_environment_version="test")
    result = SimpleNamespace(
        run_id="00000000-0000-4000-8000-000000000001",
        status=SimpleNamespace(value="succeeded"),
        promoted=True,
        model_version="model-v1",
        duration_seconds=1.0,
        candidates=(),
        dataset_identity={"version": "fixture", "sha256": "a" * 64},
        previous_model_version=None,
        mlflow_parent_run_id="mlflow-parent",
    )
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "run_pipeline", lambda *_args, **_kwargs: result)
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["event"] == "training_completed"

