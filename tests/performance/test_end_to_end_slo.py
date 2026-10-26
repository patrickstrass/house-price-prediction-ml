import os
import shutil
from time import perf_counter

import pytest
import yaml

from house_price.data.validation import load_and_validate_snapshot


@pytest.mark.performance
def test_reference_host_envelope_and_snapshot_validation_slo(dataset_paths) -> None:
    if os.getenv("CI"):
        assert (os.cpu_count() or 0) >= 4
        memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        assert memory_bytes >= 8 * 1024**3
        assert shutil.disk_usage(".").free >= 10 * 1024**3
    started = perf_counter()
    frame, _ = load_and_validate_snapshot(*dataset_paths)
    assert len(frame) == 20_640
    assert perf_counter() - started < 300


def test_documented_end_to_end_limits_and_alert_window(project_root) -> None:
    specification = (project_root / "specs/001-train-container-metrics/spec.md").read_text()
    alerts = yaml.safe_load(
        (project_root / "grafana/alerts/house-price-alerts.yaml").read_text()
    )
    assert "até 10 minutos" in specification
    assert "até 30 minutos" in specification
    assert "até 2 segundos" in specification
    group = alerts["groups"][0]
    interval_seconds = int(group["interval"].removesuffix("m")) * 60
    for rule in group["rules"]:
        hold_seconds = int(rule["for"].removesuffix("m")) * 60
        assert hold_seconds + interval_seconds <= 300

