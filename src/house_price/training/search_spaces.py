"""Versioned Optuna search spaces for each model family."""

from typing import Any

import optuna

from house_price.domain import CandidateFamily

SPACE_VERSION = "1.0.0"
LINEAR_SPACE_VERSION = "2.0.0"
LINEAR_GRID = {"fit_intercept": [True, False], "positive": [False, True]}
DEFAULT_TRIAL_BUDGETS = {
    CandidateFamily.LINEAR: 4,
    CandidateFamily.RIDGE: 12,
    CandidateFamily.LASSO: 12,
    CandidateFamily.DECISION_TREE: 16,
    CandidateFamily.RANDOM_FOREST: 10,
}


def suggest_parameters(trial: optuna.Trial, family: CandidateFamily) -> dict[str, Any]:
    if family == CandidateFamily.LINEAR:
        return {
            "fit_intercept": trial.suggest_categorical(
                "fit_intercept", LINEAR_GRID["fit_intercept"]
            ),
            "positive": trial.suggest_categorical("positive", LINEAR_GRID["positive"]),
        }
    if family == CandidateFamily.RIDGE:
        return {"alpha": trial.suggest_float("alpha", 1e-4, 100.0, log=True)}
    if family == CandidateFamily.LASSO:
        return {"alpha": trial.suggest_float("alpha", 1e-5, 10.0, log=True)}
    if family == CandidateFamily.DECISION_TREE:
        return {
            "max_depth": trial.suggest_int("max_depth", 3, 24),
            "min_samples_split": trial.suggest_int("min_samples_split", 2, 30),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 15),
        }
    if family == CandidateFamily.RANDOM_FOREST:
        return {
            "n_estimators": trial.suggest_int("n_estimators", 50, 240, step=10),
            "max_depth": trial.suggest_int("max_depth", 4, 24),
            "min_samples_split": trial.suggest_int("min_samples_split", 2, 20),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 10),
            "max_features": trial.suggest_categorical("max_features", ["sqrt", "log2", 1.0]),
        }
    raise ValueError(f"unsupported model family: {family}")
