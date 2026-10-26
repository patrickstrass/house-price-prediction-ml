import json
import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def test_azure_grafana_uses_private_host_and_full_tls() -> None:
    config = yaml.safe_load((ROOT / "grafana/datasource.azure.example.yaml").read_text())
    source = config["datasources"][0]
    assert source["user"] == "grafana_reader"
    assert source["jsonData"]["sslmode"] == "verify-full"
    assert "HOUSE_PRICE_DB_HOST" in source["url"]


def test_operational_alerts_are_declared() -> None:
    rules = yaml.safe_load((ROOT / "grafana/alerts/house-price-alerts.yaml").read_text())
    names = {rule["uid"] for group in rules["groups"] for rule in group["rules"]}
    assert {
        "house-price-weekly-stale",
        "house-price-inference-heartbeat",
        "house-price-quality-gate",
    } <= names
    monitoring = (ROOT / "infra/azure/monitoring.bicep").read_text()
    assert all(f"'{name}'" in monitoring for name in names)
    assert "logWorkspaceId" in monitoring
    assert "observability" in monitoring
    group = rules["groups"][0]
    assert group["interval"] == "1m"
    assert all(rule["for"] == "1m" for rule in group["rules"])
    for rule in group["rules"]:
        assert rule["noDataState"] in {"Alerting", "OK"}


def test_inference_dashboard_queries_metrics_by_version_and_window() -> None:
    dashboard = json.loads((ROOT / "grafana/dashboards/house-price-ml.json").read_text())
    panel = next(panel for panel in dashboard["panels"] if panel["id"] == 7)
    query = panel["targets"][0]["rawSql"]
    assert "model_version" in query
    assert "$__timeFilter(window_started_at)" in query


@pytest.mark.skipif(not os.getenv("GRAFANA_DATABASE_URL"), reason="Grafana reader URL unavailable")
def test_grafana_reader_can_query_private_observability_views() -> None:
    engine = create_engine(os.environ["GRAFANA_DATABASE_URL"], pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "grafana_reader"
        for view in (
            "grafana_training_status", "grafana_candidate_comparison",
            "grafana_inference_operations",
        ):
            connection.execute(text(f"SELECT 1 FROM observability.{view} LIMIT 1")).all()
