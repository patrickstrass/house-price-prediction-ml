"""Deterministic, resumable Optuna studies confined to the training set."""

from dataclasses import dataclass
from typing import Any

import numpy as np
import optuna
import pandas as pd
from sklearn.model_selection import cross_val_score

from house_price.domain import CandidateFamily
from house_price.training.dataset import build_estimator, training_folds
from house_price.training.search_spaces import (
    LINEAR_GRID,
    LINEAR_SPACE_VERSION,
    SPACE_VERSION,
    suggest_parameters,
)


@dataclass(frozen=True)
class OptimizationResult:
    study_name: str
    best_trial_number: int
    parameters: dict[str, Any]
    objective_rmse: float
    study: optuna.Study


def study_name(training_run_id: str, family: CandidateFamily) -> str:
    version = LINEAR_SPACE_VERSION if family == CandidateFamily.LINEAR else SPACE_VERSION
    return f"{training_run_id}:{family.value}:{version}"


def create_study(
    training_run_id: str,
    family: CandidateFamily,
    *,
    storage_url: str | None,
    seed: int = 42,
) -> optuna.Study:
    return optuna.create_study(
        study_name=study_name(training_run_id, family),
        direction="minimize",
        sampler=optuna.samplers.GridSampler(LINEAR_GRID)
        if family == CandidateFamily.LINEAR
        else optuna.samplers.TPESampler(seed=seed),
        storage=storage_url,
        load_if_exists=True,
    )


def optimize_family(
    training_run_id: str,
    family: CandidateFamily,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    storage_url: str | None,
    trial_budget: int,
    seed: int = 42,
    folds: int = 5,
    timeout: float | None = None,
) -> OptimizationResult:
    if family == CandidateFamily.LINEAR:
        trial_budget = min(trial_budget, 4)
    if trial_budget < 1:
        raise ValueError("trial_budget must be positive")
    study = create_study(training_run_id, family, storage_url=storage_url, seed=seed)

    def objective(trial: optuna.Trial) -> float:
        parameters = suggest_parameters(trial, family)
        estimator = build_estimator(family, parameters, seed=seed)
        scores = cross_val_score(
            estimator,
            x_train,
            y_train,
            cv=training_folds(seed, folds),
            scoring="neg_root_mean_squared_error",
            n_jobs=1,
        )
        value = float(-np.mean(scores))
        if not np.isfinite(value):
            raise optuna.TrialPruned("non-finite CV RMSE")
        return value

    missing = max(0, trial_budget - len(study.trials))
    if missing:
        study.optimize(objective, n_trials=missing, timeout=timeout, n_jobs=1)
    return OptimizationResult(
        study_name=study.study_name,
        best_trial_number=study.best_trial.number,
        parameters=dict(study.best_trial.params),
        objective_rmse=float(study.best_value),
        study=study,
    )
