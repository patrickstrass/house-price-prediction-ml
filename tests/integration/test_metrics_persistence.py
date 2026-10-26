from pathlib import Path

import pytest
import yaml

from house_price.api.app import InferenceMetrics


class RecordingRepository:
    def __init__(self) -> None:
        self.rows = []

    def upsert_inference_metric(self, values) -> None:
        self.rows.append(values)


class EmptyService:
    champion = None


@pytest.mark.anyio
async def test_heartbeat_persists_unavailable_without_requests() -> None:
    repository = RecordingRepository()
    metrics = InferenceMetrics("env-v1", EmptyService(), repository, window_seconds=60)
    await metrics.flush()
    assert repository.rows[0]["health_status"] == "unavailable"
    assert repository.rows[0]["request_count"] == 0
    assert repository.rows[0]["latency_p99_ms"] is None


def test_alert_state_is_evaluated_each_minute_and_survives_no_data(project_root: Path) -> None:
    alerts = yaml.safe_load(
        (project_root / "grafana/alerts/house-price-alerts.yaml").read_text()
    )
    assert alerts["groups"][0]["interval"] == "1m"
    heartbeat = next(
        rule
        for rule in alerts["groups"][0]["rules"]
        if rule["uid"] == "house-price-inference-heartbeat"
    )
    assert heartbeat["noDataState"] == "Alerting"
    pending_minutes = int(heartbeat["for"].removesuffix("m"))
    assert pending_minutes + 1 <= 5

