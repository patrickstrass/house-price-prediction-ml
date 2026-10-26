#!/usr/bin/env python3
"""Create and validate the immutable weekly evidence manifest."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nullable(value: str) -> str | None:
    return None if value in ("", "null", "none") else value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--training-run-id", default="null")
    parser.add_argument("--previous-model-version", default="null")
    parser.add_argument("--candidate-model-version", default="null")
    parser.add_argument("--effective-model-version", default="null")
    parser.add_argument(
        "--deployment-health", choices=("passed", "failed", "not_run"), default="not_run"
    )
    parser.add_argument(
        "--rollback-result",
        choices=("passed", "failed", "not_required", "not_run"),
        default="not_run",
    )
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--azure-result", type=Path)
    parser.add_argument("--output", type=Path, default=Path("weekly-output/weekly-evidence.json"))
    args = parser.parse_args()
    azure_result = {}
    if args.azure_result and args.azure_result.is_file():
        azure_result = json.loads(args.azure_result.read_text())
    paths = [*args.artifact]
    if args.azure_result:
        paths.append(args.azure_result)
    artifacts = {path.name: checksum(path) for path in paths if path.is_file()}
    if not artifacts:
        sentinel = args.output.parent / "no-artifacts.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("workflow failed before backup\n")
        artifacts[sentinel.name] = checksum(sentinel)
    evidence = {
        "schema_version": "1.0.0",
        "workflow_run_id": args.workflow_run_id,
        "training_run_id": nullable(args.training_run_id) or azure_result.get("training_run_id"),
        "previous_model_version": nullable(args.previous_model_version)
        or azure_result.get("previous_model_version"),
        "candidate_model_version": nullable(args.candidate_model_version)
        or azure_result.get("candidate_model_version"),
        "effective_model_version": nullable(args.effective_model_version)
        or azure_result.get("effective_model_version"),
        "deployment_health": args.deployment_health,
        "rollback_result": args.rollback_result,
        "artifact_checksums": artifacts,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    schema_path = Path("specs/001-train-container-metrics/contracts/weekly-evidence.schema.json")
    schema = json.loads(schema_path.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

