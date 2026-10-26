import json
from pathlib import Path

import yaml

from scripts.validate_grafana_queries import validate


def test_dashboard_contains_required_metrics_and_panels(project_root: Path) -> None:
    path = project_root / "grafana/dashboards/house-price-ml.json"
    dashboard = json.loads(path.read_text())
    assert len(dashboard["panels"]) == 8
    serialized = json.dumps(dashboard)
    for token in ("mae", "mse", "rmse", "r2", "p50", "p95", "p99", "error_count"):
        assert token.lower() in serialized.lower()
    assert "five candidate" in serialized.lower()


def test_alerts_cover_weekly_inference_quality_and_no_data(project_root: Path) -> None:
    alerts = yaml.safe_load(
        (project_root / "grafana/alerts/house-price-alerts.yaml").read_text()
    )
    rules = {rule["uid"]: rule for rule in alerts["groups"][0]["rules"]}
    assert set(rules) == {
        "house-price-weekly-stale",
        "house-price-inference-heartbeat",
        "house-price-quality-gate",
    }
    assert rules["house-price-inference-heartbeat"]["noDataState"] == "Alerting"
    pending_minutes = int(rules["house-price-inference-heartbeat"]["for"].removesuffix("m"))
    interval_minutes = int(alerts["groups"][0]["interval"].removesuffix("m"))
    assert pending_minutes + interval_minutes <= 5
    validate(project_root)

