"""Fail-closed subscription and cost preflight for a future deployment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from house_price.azure.evidence import now_utc

REQUIRED_PROVIDERS = (
    "Microsoft.App",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.Storage",
    "Microsoft.ContainerRegistry",
)
REQUIRED_RESOURCES = (
    "container_apps_compute",
    "container_apps_requests",
    "postgres",
    "container_registry",
    "file_shares",
    "backup_storage",
    "logs",
    "network",
)


def build_report(
    *,
    subscription: dict[str, Any],
    region: str,
    credit: dict[str, Any],
    prices: dict[str, Any],
    providers: list[dict[str, Any]],
    quotas: list[dict[str, Any]],
    operational_limit: Decimal,
    safety_margin: Decimal,
    feature_002_gate: str = "pending",
) -> dict[str, Any]:
    """Build evidence from supplied facts; never silently infer missing credit/prices."""
    reasons: list[str] = []
    offer = str(subscription.get("offer_type") or "unknown")
    if "student" not in offer.lower():
        reasons.append("offer_not_student")
    if str(credit.get("subscription_id")) != str(subscription.get("id")):
        reasons.append("credit_subscription_mismatch")
    currency = str(credit.get("currency") or "XXX")
    if currency != prices.get("currency"):
        reasons.append("currency_mismatch")
    if prices.get("region", "").lower() != region.lower():
        reasons.append("price_region_mismatch")
    if not credit.get("source") or not credit.get("checked_at"):
        reasons.append("credit_unverified")
    if not prices.get("checked_at"):
        reasons.append("price_unverified")
    for label, value in (("credit", credit.get("checked_at")), ("price", prices.get("checked_at"))):
        try:
            checked = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if checked.tzinfo is None or not datetime.now(UTC) - timedelta(
                days=1
            ) <= checked <= datetime.now(UTC):
                reasons.append(f"{label}_stale")
        except ValueError:
            reasons.append(f"{label}_unverified")
    estimates = prices.get("resources") or []
    covered = {item.get("resource_type") for item in estimates}
    if any(resource not in covered for resource in REQUIRED_RESOURCES):
        reasons.append("price_incomplete")
    for provider in providers:
        if provider.get("registration_state") != "Registered" or not provider.get(
            "regional_availability"
        ):
            reasons.append("provider_unavailable")
            break
    if {p.get("namespace") for p in providers} < set(REQUIRED_PROVIDERS):
        reasons.append("provider_incomplete")
    if not quotas or any(item.get("available", -1) < item.get("required", 0) for item in quotas):
        reasons.append("quota_insufficient")
    total = sum((Decimal(str(item["monthly_estimate"])) for item in estimates), Decimal(0))
    known_balance = credit.get("remaining") is not None
    balance = Decimal(str(credit.get("remaining") if known_balance else 0))
    if not known_balance:
        reasons.append("credit_unverified")
    if total + safety_margin > balance or total + safety_margin > operational_limit:
        reasons.append("cost_exceeds_limit")
    if operational_limit < 0 or safety_margin < 0 or balance < 0:
        reasons.append("negative_amount")
    return {
        "schema_version": "1.0.0",
        "preflight_id": str(uuid4()),
        "checked_at": now_utc(),
        "subscription_id": str(subscription["id"]),
        "offer_type": offer,
        "region": region,
        "providers": providers,
        "currency": currency,
        "credit_remaining": float(balance) if known_balance else None,
        "credit_source": str(credit.get("source") or "missing"),
        "credit_checked_at": credit.get("checked_at"),
        "cost_estimate_30d": float(total),
        "safety_margin": float(safety_margin),
        "operational_limit": float(operational_limit),
        "price_checked_at": prices.get("checked_at"),
        "resource_estimates": estimates,
        "quotas": quotas,
        "assumptions": list(prices.get("assumptions") or []),
        "feature_002_gate": feature_002_gate,
        "status": "failed" if reasons else "passed",
        "reason_codes": sorted(set(reasons)),
    }
