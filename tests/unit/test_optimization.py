import numpy as np
import pandas as pd

from house_price.domain import FEATURE_NAMES, CandidateFamily
from house_price.training.optimization import create_study, optimize_family, study_name


def test_study_name_and_sampler_are_deterministic() -> None:
    name = study_name("run-1", CandidateFamily.RIDGE)
    assert name == "run-1:ridge:1.0.0"
    study = create_study("run-1", CandidateFamily.RIDGE, storage_url=None)
    assert study.study_name == name
    assert study.direction.name == "MINIMIZE"


def test_linear_study_explores_all_four_parameter_combinations() -> None:
    rng = np.random.default_rng(42)
    x = pd.DataFrame(rng.normal(size=(80, 8)), columns=FEATURE_NAMES)
    y = pd.Series(rng.normal(size=80))
    result = optimize_family(
        "linear-run",
        CandidateFamily.LINEAR,
        x,
        y,
        storage_url=None,
        trial_budget=9,
        folds=2,
    )
    assert len(result.study.trials) == 4
    assert {
        (trial.params["fit_intercept"], trial.params["positive"])
        for trial in result.study.trials
    } == {(True, False), (True, True), (False, False), (False, True)}
    assert set(result.parameters) == {"fit_intercept", "positive"}

