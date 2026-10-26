import pytest

from house_price.azure.cost import compare_cost
from scripts import azure_apply


def test_observed_cost_never_claims_hard_cap() -> None:
    preflight = {
        "subscription_id": "test",
        "currency": "USD",
        "operational_limit": 10,
        "credit_remaining": 8,
    }
    observed = {
        "subscription_id": "test",
        "currency": "USD",
        "checked_at": "2026-10-25T00:00:00Z",
        "source": "cost-export",
        "window_start": "2026-10-01",
        "window_end": "2026-10-25",
        "resources": [{"cost": 9}],
    }
    result = compare_cost(preflight, observed)
    assert result["status"] == "over_limit"
    assert "do not stop" in result["note"]


def test_mismatched_currency_fails() -> None:
    with pytest.raises(ValueError):
        compare_cost(
            {"subscription_id": "s", "currency": "USD"}, {"subscription_id": "s", "currency": "BRL"}
        )


def test_cost_report_compares_resources_and_flags_missing_export() -> None:
    from datetime import UTC, datetime

    preflight = {
        "subscription_id": "s", "currency": "USD", "operational_limit": 20,
        "credit_remaining": 20,
        "resource_estimates": [{"resource_type": "postgres", "monthly_estimate": 8}],
    }
    missing = compare_cost(preflight, {"subscription_id": "s", "currency": "USD"})
    assert missing["status"] == "unavailable"
    result = compare_cost(preflight, {
        "subscription_id": "s", "currency": "USD",
        "checked_at": datetime.now(UTC).isoformat(), "source": "export",
        "window_start": "2026-10-01", "window_end": "2026-10-25",
        "resources": [{"resource_type": "postgres", "cost": 3}],
    })
    assert result["resources"] == [
        {"resource_type": "postgres", "observed": 3.0, "estimated_30d": 8.0}
    ]


def test_apply_wrapper_never_provisions_without_explicit_flag(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from house_price.azure.evidence import write_json
    from house_price.azure.preflight import REQUIRED_PROVIDERS, REQUIRED_RESOURCES, build_report

    sid = str(uuid4())
    stamp = datetime.now(UTC).isoformat()
    report = build_report(
        subscription={"id": sid, "offer_type": "Azure for Students"},
        region="brazilsouth",
        credit={"subscription_id": sid, "remaining": 100, "currency": "USD",
                "source": "portal", "checked_at": stamp},
        prices={"currency": "USD", "region": "brazilsouth", "checked_at": stamp, "resources": [
            {"resource_type": name, "sku": "sample", "monthly_estimate": 1,
             "source": "calculator"} for name in REQUIRED_RESOURCES]},
        providers=[{"namespace": name, "registration_state": "Registered",
                    "regional_availability": True} for name in REQUIRED_PROVIDERS],
        quotas=[{"name": "cores", "required": 1, "available": 2, "unit": "count"}],
        operational_limit=Decimal(50), safety_margin=Decimal(5), feature_002_gate="passed",
    )
    path = tmp_path / "preflight.json"
    write_json(path, report, "azure-preflight.schema.json")
    monkeypatch.setattr(azure_apply, "az_run", lambda *_args: pytest.fail("Azure was called"))
    monkeypatch.setattr("sys.argv", ["azure_apply.py", "--preflight", str(path),
                                    "--subscription", sid, "--resource-group", "test",
                                    "--parameters", str(tmp_path / "parameters.json")])
    assert azure_apply.main() == 0


def test_deployment_parameters_reject_mutable_or_placeholder_images(tmp_path) -> None:
    import json

    path = tmp_path / "parameters.json"
    values = {
        name: {"value": f"example.azurecr.io/{service}:sha-{'a' * 40}-run-1"}
        for name, service in (
            ("trainerImage", "trainer"),
            ("inferenceImage", "inference"),
            ("mlflowImage", "mlflow"),
        )
    }
    path.write_text(json.dumps({"parameters": values}))
    azure_apply.check_deployment_parameters(path)
    values["inferenceImage"]["value"] = "example.azurecr.io/inference:latest"
    path.write_text(json.dumps({"parameters": values}))
    with pytest.raises(ValueError, match="unique CI image tag"):
        azure_apply.check_deployment_parameters(path)
