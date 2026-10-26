"""Build a preflight report from operator-supplied, auditable facts."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from house_price.azure.evidence import read_json, write_json
from house_price.azure.preflight import build_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--facts", type=Path, required=True, help="read-only subscription/provider/quota snapshot"
    )
    parser.add_argument("--credit-evidence", type=Path, required=True)
    parser.add_argument(
        "--prices", type=Path, required=True, help="dated regional pricing estimate"
    )
    parser.add_argument("--operational-limit", type=Decimal, required=True)
    parser.add_argument("--safety-margin", type=Decimal, required=True)
    parser.add_argument(
        "--feature-002-gate", choices=("pending", "passed", "failed"), default="pending"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    facts = read_json(args.facts)
    subscription = facts["subscription"]
    if subscription["id"] != args.subscription:
        parser.error("facts subscription does not match --subscription")
    report = build_report(
        subscription=subscription,
        region=args.region,
        credit=read_json(args.credit_evidence),
        prices=read_json(args.prices),
        providers=facts["providers"],
        quotas=facts["quotas"],
        operational_limit=args.operational_limit,
        safety_margin=args.safety_margin,
        feature_002_gate=args.feature_002_gate,
    )
    write_json(args.output, report, "azure-preflight.schema.json")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
