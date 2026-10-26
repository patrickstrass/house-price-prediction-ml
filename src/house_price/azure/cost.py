"""Compare dated observed costs with a preflight budget; no Azure API calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any


def compare_cost(preflight: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    if preflight.get("status", "passed") != "passed":
        raise ValueError("approved preflight required for cost comparison")
    if preflight["subscription_id"] != observed["subscription_id"]:
        raise ValueError("subscription mismatch")
    if preflight["currency"] != observed["currency"]:
        raise ValueError("currency mismatch")
    checked_at = observed.get("checked_at")
    source = observed.get("source")
    if not checked_at or not source or "resources" not in observed:
        return {
            "subscription_id": preflight["subscription_id"],
            "currency": preflight["currency"],
            "status": "unavailable",
            "reason_code": "cost_export_missing",
            "note": "Azure budgets notify; they do not stop consumption",
        }
    checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise ValueError("cost timestamp must have a UTC offset")
    if checked > datetime.now(UTC):
        raise ValueError("cost timestamp cannot be in the future")
    by_resource: dict[str, Decimal] = {}
    for item in observed["resources"]:
        name = str(item.get("resource_type") or "unattributed")
        cost = Decimal(str(item["cost"]))
        if cost < 0:
            raise ValueError("observed cost cannot be negative")
        by_resource[name] = by_resource.get(name, Decimal()) + cost
    actual = sum(by_resource.values(), Decimal())
    estimates = {
        item["resource_type"]: Decimal(str(item["monthly_estimate"]))
        for item in preflight.get("resource_estimates", [])
    }
    limit = min(
        Decimal(str(preflight["operational_limit"])), Decimal(str(preflight["credit_remaining"]))
    )
    return {
        "subscription_id": preflight["subscription_id"],
        "currency": observed["currency"],
        "window_start": observed["window_start"],
        "window_end": observed["window_end"],
        "checked_at": observed["checked_at"],
        "source": source,
        "observed_total": float(actual),
        "limit": float(limit),
        "resources": [
            {
                "resource_type": name,
                "observed": float(cost),
                "estimated_30d": float(estimates[name]) if name in estimates else None,
            }
            for name, cost in sorted(by_resource.items())
        ],
        "status": "stale" if datetime.now(UTC) - checked > timedelta(days=1)
        else "over_limit" if actual > limit else "within_limit",
        "note": "Azure budgets notify; they do not stop consumption",
    }
