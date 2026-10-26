"""Guarded future release, verification, and champion/revision rollback."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from house_price.azure.cli import az_json, az_run
from house_price.azure.evidence import now_utc, read_json, validate, write_json
from house_price.azure.release import check_catalog_evidence, prepare_release, verify_http


def _revision_url(app: str, group: str, revision: str) -> str:
    response = az_json(
        "containerapp",
        "revision",
        "show",
        "--name",
        app,
        "--resource-group",
        group,
        "--revision",
        revision,
    )
    fqdn = response.get("properties", {}).get("fqdn")
    if not fqdn:
        raise ValueError("Azure did not report a revision-specific FQDN")
    return "https://" + fqdn


def _check_acr(registry: str, images: dict) -> None:
    for service, image in images.items():
        reference = image["tag"].split("/", 1)[-1]
        response = az_json(
            "acr", "manifest", "show-metadata", "--registry", registry, "--name", reference
        )
        if response.get("digest") != image["digest"]:
            raise ValueError(f"ACR digest mismatch for {service}")


def _check_revision_image(app: str, group: str, revision: str, expected: str) -> None:
    response = az_json(
        "containerapp", "revision", "show", "--name", app,
        "--resource-group", group, "--revision", revision,
    )
    containers = response.get("properties", {}).get("template", {}).get("containers", [])
    if len(containers) != 1 or containers[0].get("image") != expected:
        raise ValueError("deployed revision image differs from the approved tag")


def _restore_champion_job(group: str, app: str, previous_release_id: str) -> None:
    prefix = app.removesuffix("-inference")
    if prefix == app:
        raise ValueError("inference app name must end in -inference")
    job = f"{prefix}-release-rollback"
    response = az_json(
        "containerapp", "job", "start", "--name", job,
        "--resource-group", group, "--args", "--previous-release",
        f"/backup/releases/{previous_release_id}.json",
    )
    execution = response.get("name") or response.get("metadata", {}).get("name")
    if not execution:
        raise ValueError("rollback job did not return an execution ID")
    # Leave time within the 15-minute rollback objective for revision and HTTP checks.
    deadline = time.monotonic() + 13 * 60
    while time.monotonic() < deadline:
        state = az_json(
            "containerapp", "job", "execution", "show", "--name", job,
            "--resource-group", group, "--job-execution-name", str(execution),
        )
        status = state.get("properties", {}).get("status") or state.get("status")
        if status == "Succeeded":
            return
        if status in {"Failed", "Canceled"}:
            raise ValueError("rollback champion job failed")
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    raise TimeoutError("rollback champion job did not finish")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("deploy", "verify", "rollback"))
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--storage-probe", type=Path)
    parser.add_argument("--ci-manifest", type=Path)
    parser.add_argument("--target-config", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--previous-release", type=Path)
    parser.add_argument("--catalog-evidence", type=Path)
    parser.add_argument("--resource-group")
    parser.add_argument("--app")
    parser.add_argument("--acr-name")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "deploy":
        inputs = (args.preflight, args.storage_probe, args.ci_manifest, args.target_config)
        if any(value is None for value in inputs):
            parser.error("deploy requires preflight, storage-probe, ci-manifest, target-config")
        record = prepare_release(
            preflight_path=args.preflight,
            storage_path=args.storage_probe,
            manifest_path=args.ci_manifest,
            target_config_path=args.target_config,
        )
        if args.execute:
            if not all((args.resource_group, args.app, args.acr_name)):
                parser.error("--execute requires --resource-group, --app and --acr-name")
            try:
                _check_acr(args.acr_name, record["images"])
                image = record["images"]["inference"]["tag"]
                response = az_json(
                    "containerapp", "update", "--name", args.app,
                    "--resource-group", args.resource_group, "--image", image,
                )
                revision = response.get("properties", {}).get("latestRevisionName")
                if not revision:
                    raise ValueError("Azure did not return the new revision")
                _check_revision_image(args.app, args.resource_group, revision, image)
                record["inference_revision"] = revision
                record["status"] = "deployed"
            except Exception:
                record.update(status="failed", completed_at=now_utc())
                write_json(args.output, record, "azure-release.schema.json")
                raise
    else:
        if not all((args.release, args.resource_group, args.app)):
            parser.error("verify/rollback require --release, --resource-group and --app")
        record = read_json(args.release)
        validate(record, "azure-release.schema.json")
        if args.command == "verify":
            if record["status"] != "deployed":
                parser.error("only deployed releases can be verified")
            if not args.execute:
                parser.error("verify requires --execute before routing verified traffic")
            if not args.catalog_evidence:
                parser.error("verify requires --catalog-evidence from a successful ACA verify job")
            try:
                checked = verify_http(
                    _revision_url(args.app, args.resource_group, record["inference_revision"]),
                    record,
                )
                check_catalog_evidence(record, read_json(args.catalog_evidence))
                az_run(
                    "containerapp", "ingress", "traffic", "set", "--name", args.app,
                    "--resource-group", args.resource_group, "--revision-weight",
                    f"{record['inference_revision']}=100",
                )
                record = checked
            except Exception:
                record.update(status="failed", health_result="failed",
                              prediction_result="failed", completed_at=now_utc())
                write_json(args.output, record, "azure-release.schema.json")
                raise
        else:
            if not args.execute or not args.previous_release:
                parser.error("rollback requires --execute and --previous-release")
            previous = read_json(args.previous_release)
            validate(previous, "azure-release.schema.json")
            if (
                previous["status"] != "verified"
                or record["previous_release_id"] != previous["release_id"]
            ):
                parser.error("previous release is not verified or does not match")
            try:
                _restore_champion_job(args.resource_group, args.app, previous["release_id"])
                az_run(
                    "containerapp", "revision", "activate", "--name", args.app,
                    "--resource-group", args.resource_group, "--revision",
                    previous["inference_revision"],
                )
                # Shared registry may have changed. Refuse traffic until model recovery.
                verify_http(
                    _revision_url(args.app, args.resource_group, previous["inference_revision"]),
                    previous,
                )
                az_run(
                    "containerapp", "ingress", "traffic", "set", "--name", args.app,
                    "--resource-group", args.resource_group, "--revision-weight",
                    f"{previous['inference_revision']}=100",
                )
                record.update(
                    status="rolled_back", rollback_result="passed",
                    health_result="passed", prediction_result="passed",
                    completed_at=now_utc(),
                )
            except Exception:
                record.update(status="failed", rollback_result="failed", completed_at=now_utc())
                write_json(args.output, record, "azure-release.schema.json")
                raise
    write_json(args.output, record, "azure-release.schema.json")
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
