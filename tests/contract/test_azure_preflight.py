from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from house_price.azure.evidence import validate
from house_price.azure.preflight import REQUIRED_PROVIDERS, REQUIRED_RESOURCES, build_report


@pytest.fixture
def facts() -> dict:
    sid = str(uuid4())
    checked_at = datetime.now(UTC).isoformat()
    return {
        "subscription": {"id": sid, "offer_type": "Azure for Students"},
        "region": "brazilsouth",
        "credit": {
            "subscription_id": sid,
            "remaining": 100,
            "currency": "USD",
            "source": "portal",
            "checked_at": checked_at,
        },
        "prices": {
            "currency": "USD", "region": "brazilsouth",
            "checked_at": checked_at,
            "resources": [
                {
                    "resource_type": x,
                    "sku": "example",
                    "monthly_estimate": 1,
                    "source": "pricing-calculator",
                }
                for x in REQUIRED_RESOURCES
            ],
        },
        "providers": [
            {"namespace": x, "registration_state": "Registered", "regional_availability": True}
            for x in REQUIRED_PROVIDERS
        ],
        "quotas": [{"name": "cores", "required": 1, "available": 2, "unit": "count"}],
    }


def report(facts: dict) -> dict:
    return build_report(**facts, operational_limit=Decimal(80), safety_margin=Decimal(5))


def test_valid_facts_pass(facts: dict) -> None:
    result = report(facts)
    assert result["status"] == "passed"
    validate(result, "azure-preflight.schema.json")


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda f: f["subscription"].update(offer_type="Pay as you go"), "offer_not_student"),
        (lambda f: f["credit"].update(remaining=0), "cost_exceeds_limit"),
        (lambda f: f["quotas"][0].update(available=0), "quota_insufficient"),
        (lambda f: f["prices"].update(checked_at=None), "price_unverified"),
        (lambda f: f["prices"].update(checked_at="2000-01-01T00:00:00Z"), "price_stale"),
        (lambda f: f["credit"].update(remaining=None), "credit_unverified"),
    ],
)
def test_bad_facts_fail(facts: dict, mutation, reason: str) -> None:
    mutation(facts)
    result = report(facts)
    assert result["status"] == "failed"
    assert reason in result["reason_codes"]
    validate(result, "azure-preflight.schema.json")


def test_missing_provider_and_prices_still_produces_valid_failed_report(facts: dict) -> None:
    facts["providers"] = []
    facts["prices"]["resources"] = []
    result = report(facts)
    assert result["status"] == "failed"
    validate(result, "azure-preflight.schema.json")
