"""Guarded future Bicep deployment. Never called by CI automatically."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from house_price.azure.cli import az_run
from house_price.azure.evidence import read_json, validate
from house_price.azure.release import _fresh

_APPROVED_TAG = re.compile(
    r"^[a-z0-9.-]+\.azurecr\.io/(trainer|inference|mlflow):sha-[0-9a-f]{40}-run-[0-9]+$"
)


def check_deployment_parameters(path: Path) -> None:
    values = read_json(path)["parameters"]
    images = (
        ("trainerImage", "trainer"),
        ("inferenceImage", "inference"),
        ("mlflowImage", "mlflow"),
    )
    for name, service in images:
        image = values[name]["value"]
        match = _APPROVED_TAG.fullmatch(image)
        if match is None or match.group(1) != service:
            raise ValueError(f"{name} must use a unique CI image tag")
    for name, parameter in values.items():
        if name == "postgresAdminPassword":
            raise ValueError("database password must be supplied as a secure deployment parameter")
        if "REPLACE" in str(parameter.get("value", "")):
            raise ValueError(f"{name} contains a placeholder")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument(
        "--execute", action="store_true", help="explicitly permit resource creation"
    )
    args = parser.parse_args()
    report = read_json(args.preflight)
    validate(report, "azure-preflight.schema.json")
    if report["status"] != "passed" or report["subscription_id"] != args.subscription:
        parser.error("preflight failed or subscription mismatch")
    if report["feature_002_gate"] != "passed":
        parser.error("feature 002 is not verified")
    if not _fresh(report["checked_at"]):
        parser.error("preflight report is stale")
    if not args.execute:
        print("validated only; add --execute in a separately authorized deployment")
        return 0
    check_deployment_parameters(args.parameters)
    az_run(
        "deployment",
        "group",
        "create",
        "--subscription",
        args.subscription,
        "--resource-group",
        args.resource_group,
        "--template-file",
        "infra/azure/main.bicep",
        "--parameters",
        f"@{args.parameters}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
