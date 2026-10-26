from types import SimpleNamespace

from house_price.domain import CandidateFamily, ModelCandidate, ModelMetrics
from house_price.training.tracking import TrainingTracker


def test_mlflow_selection_identifies_winning_family_and_effective_champion(monkeypatch) -> None:
    tagged = {}
    artifacts = []
    metrics = {}
    client = SimpleNamespace(set_run_tags=lambda run_id, tags: tagged.update({run_id: tags}))
    monkeypatch.setattr("house_price.training.tracking.mlflow.log_dict",
                        lambda document, path: artifacts.append((path, document)))
    monkeypatch.setattr("house_price.training.tracking.mlflow.log_metric",
                        lambda name, value: metrics.update({name: value}))
    candidates = [
        ModelCandidate(
            training_run_id="run", family=family, mlflow_run_id=family.value,
            best_trial_number=0, parameters={},
            metrics=ModelMetrics(mae=mae, mse=mae * mae, rmse=mae, r2=0.8),
            eligible=True,
        )
        for family, mae in ((CandidateFamily.LINEAR, 0.7), (CandidateFamily.RANDOM_FOREST, 0.5))
    ]
    TrainingTracker(client).record_selection(
        parent_run_id="parent", candidates=candidates, winner=candidates[1],
        decision="promoted", effective_model_version="version-2",
    )
    assert tagged["parent"]["selection.winner_family"] == "random_forest"
    assert metrics["selection.winner_holdout_rmse"] == 0.5
    assert metrics["selection.family_count"] == 2
    assert tagged["linear"]["selection.winner"] == "false"
    assert tagged["random_forest"]["selection.winner"] == "true"
    assert artifacts[0][0] == "selection/family-comparison.json"
    assert artifacts[0][1]["effective_model_version"] == "version-2"
    assert sum(item["winner"] for item in artifacts[0][1]["candidates"]) == 1
