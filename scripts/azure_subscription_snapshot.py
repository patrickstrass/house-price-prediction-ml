"""Collect read-only subscription/provider facts for a future preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from house_price.azure.cli import az_json
from house_price.azure.evidence import read_json, write_json

PROVIDERS = {
    "Microsoft.App": "managedEnvironments",
    "Microsoft.DBforPostgreSQL": "flexibleServers",
    "Microsoft.Storage": "storageAccounts",
    "Microsoft.ContainerRegistry": "registries",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--offer-type", required=True, help="offer evidence checked in Azure portal"
    )
    parser.add_argument("--quotas", type=Path, required=True, help="dated quota evidence JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-only-query", action="store_true")
    args = parser.parse_args()
    if not args.read_only_query:
        parser.error("--read-only-query is required; this command never provisions resources")
    account = az_json("account", "show", "--subscription", args.subscription)
    if account.get("id") != args.subscription:
        parser.error("active account does not match requested subscription")
    providers = []
    for namespace, resource_type in PROVIDERS.items():
        response = az_json(
            "provider", "show", "--namespace", namespace, "--subscription", args.subscription
        )
        supported = False
        for item in response.get("resourceTypes", []):
            locations = {value.lower().replace(" ", "") for value in item.get("locations", [])}
            if item.get("resourceType", "").lower() == resource_type.lower():
                supported = args.region.lower() in locations
                break
        providers.append(
            {
                "namespace": namespace,
                "registration_state": response.get("registrationState", "Unknown"),
                "regional_availability": supported,
            }
        )
    quota_evidence = read_json(args.quotas)
    if quota_evidence.get("subscription_id") != args.subscription:
        parser.error("quota evidence subscription mismatch")
    if quota_evidence.get("region", "").lower() != args.region.lower():
        parser.error("quota evidence region mismatch")
    if not quota_evidence.get("checked_at") or not quota_evidence.get("source"):
        parser.error("quota evidence needs source and timestamp")
    result = {
        "subscription": {"id": args.subscription, "offer_type": args.offer_type},
        "providers": providers,
        "quotas": quota_evidence["quotas"],
    }
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
