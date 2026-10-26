"""Preview jobs locally; --execute runs them only in a future authorized deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from house_price.azure.cli import az_json, az_run
from house_price.azure.evidence import read_json, validate, write_json
from house_price.azure.jobs import JOB_ORDER, run_sequence, training_uuid
from house_price.azure.release import _fresh


def parse_job_event(output: str, event: str, *, training_run_id: str | None = None) -> dict:
    """Extract one structured terminal line from ACA log output without copying raw logs."""
    matches = []
    for line in output.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            item = json.loads(line[start:])
            if isinstance(item, dict) and "Log" in item:
                item = json.loads(item["Log"])
        except (ValueError, TypeError):
            continue
        if not isinstance(item, dict) or item.get("event") != event:
            continue
        if training_run_id and item.get("training_run_id") != training_run_id:
            continue
        matches.append(item)
    if len(matches) != 1:
        raise ValueError(f"expected one {event} event for this job execution")
    return matches[0]


def job_event(job: str, execution_id: str, *, prefix: str, group: str,
              event: str, training_run_id: str | None = None) -> dict:
    for attempt in range(6):
        completed = subprocess.run(
            ["az", "containerapp", "job", "logs", "show", "--name", f"{prefix}-{job}",
             "--resource-group", group, "--container", job, "--execution", execution_id,
             "--format", "json", "--tail", "300", "--only-show-errors"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if completed.returncode == 0:
            try:
                return parse_job_event(
                    completed.stdout, event, training_run_id=training_run_id
                )
            except ValueError:
                pass
        if attempt < 5:
            time.sleep(10)
    raise ValueError(f"{job} terminal evidence is unavailable")


def set_mlflow_running(*, subscription: str, group: str, prefix: str,
                       running: bool) -> None:
    """Stop MLflow writers for the correlated backup and restart before reconciliation."""
    app = f"{prefix}-mlflow"
    action = "start" if running else "stop"
    url = (
        f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{group}"
        f"/providers/Microsoft.App/containerApps/{app}/{action}?api-version=2025-07-01"
    )
    az_run("rest", "--method", "post", "--url", url, "--output", "none")
    expected = "Running" if running else "Stopped"
    for _ in range(36):
        response = az_json(
            "containerapp", "show", "--name", app, "--resource-group", group,
            "--subscription", subscription,
        )
        if response.get("properties", {}).get("runningStatus") == expected:
            return
        time.sleep(10)
    raise TimeoutError(f"MLflow did not reach {expected} state")


def job_start_command(
    job: str, *, prefix: str, resource_group: str, subscription: str,
    repository: str, github_run_id: str,
) -> list[str]:
    command = [
        "containerapp", "job", "start", "--name", f"{prefix}-{job}",
        "--resource-group", resource_group, "--subscription", subscription,
    ]
    if job == "trainer":
        command.extend(("--args", "--run-id", training_uuid(repository, github_run_id)))
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--storage-probe", type=Path)
    parser.add_argument("--resource-group")
    parser.add_argument("--prefix")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        if not all((args.preflight, args.storage_probe, args.resource_group, args.prefix)):
            parser.error("--execute requires preflight, storage-probe, resource-group and prefix")
        preflight = read_json(args.preflight)
        validate(preflight, "azure-preflight.schema.json")
        if preflight["status"] != "passed" or preflight["feature_002_gate"] != "passed":
            parser.error("preflight/feature 002 gate failed")
        if not _fresh(preflight["checked_at"]):
            parser.error("preflight is stale")
        probe = read_json(args.storage_probe)
        if (
            probe.get("status") != "passed"
            or probe.get("preflight_id") != preflight["preflight_id"]
        ):
            parser.error("real ACA storage probe is not approved")

        execution_ids: dict[str, str] = {}
        mlflow_stopped = False

        def start(job: str) -> str:
            nonlocal mlflow_stopped
            if job == "backup":
                mlflow_stopped = True
                set_mlflow_running(
                    subscription=preflight["subscription_id"], group=args.resource_group,
                    prefix=args.prefix, running=False,
                )
            elif job == "catalog-reconcile" and mlflow_stopped:
                set_mlflow_running(
                    subscription=preflight["subscription_id"], group=args.resource_group,
                    prefix=args.prefix, running=True,
                )
                mlflow_stopped = False
            response = az_json(*job_start_command(
                job,
                prefix=args.prefix,
                resource_group=args.resource_group,
                subscription=preflight["subscription_id"],
                repository=args.repository,
                github_run_id=args.github_run_id,
            ))
            execution = response.get("name") or response.get("metadata", {}).get("name")
            if not execution:
                raise ValueError("Azure did not return a job execution name")
            execution_ids[job] = str(execution)
            return str(execution)

        def inspect(job: str, execution_id: str) -> str:
            response = az_json(
                "containerapp",
                "job",
                "execution",
                "show",
                "--name",
                f"{args.prefix}-{job}",
                "--resource-group",
                args.resource_group,
                "--job-execution-name",
                execution_id,
                "--subscription",
                preflight["subscription_id"],
            )
            return str(response.get("properties", {}).get("status") or response.get("status"))

        def summarize(training_run_id: str) -> dict:
            backup = job_event(
                "backup", execution_ids["backup"], prefix=args.prefix,
                group=args.resource_group, event="backup_completed",
            )
            training = job_event(
                "trainer", execution_ids["trainer"], prefix=args.prefix,
                group=args.resource_group, event="training_completed",
                training_run_id=training_run_id,
            )
            return {
                **training,
                "backup_set_id": backup["backup_set_id"],
                "evidence_uri": (
                    f"https://github.com/{args.repository}/actions/runs/{args.github_run_id}"
                ),
            }

        result = {"status": "failed", "reason_code": "job_sequence_interrupted"}
        try:
            result = run_sequence(
                repository=args.repository,
                github_run_id=args.github_run_id,
                start=start,
                inspect=inspect,
                summarize=summarize,
                evidence_uri=f"https://github.com/{args.repository}/actions/runs/{args.github_run_id}",
            )
        finally:
            if mlflow_stopped:
                try:
                    set_mlflow_running(
                        subscription=preflight["subscription_id"], group=args.resource_group,
                        prefix=args.prefix, running=True,
                    )
                except Exception:
                    result["status"] = "failed"
                    result["reason_code"] = "mlflow_restart_failed"
        if (
            result["status"] == "failed"
            and "backup" in execution_ids
            and result["job_executions"][0]["status"] == "Succeeded"
        ):
            try:
                backup = job_event(
                    "backup", execution_ids["backup"], prefix=args.prefix,
                    group=args.resource_group, event="backup_completed",
                )
                result["backup_set_id"] = backup["backup_set_id"]
                result["previous_model_version"] = backup["model_version"]
                result["effective_model_version"] = backup["model_version"]
            except ValueError:
                pass
            if "trainer" in execution_ids:
                try:
                    job_event(
                        "trainer", execution_ids["trainer"], prefix=args.prefix,
                        group=args.resource_group, event="dataset_invalid",
                    )
                    result["promotion_decision"] = "dataset_invalid"
                except ValueError:
                    pass
        result["mode"] = "azure"
    else:
        result = run_sequence(
            repository=args.repository,
            github_run_id=args.github_run_id,
            start=lambda job: f"simulated-{job}",
            inspect=lambda _job, _id: "Succeeded",
        )
        result["mode"] = "simulation"
    result["job_order"] = list(JOB_ORDER)
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "succeeded" else 4


if __name__ == "__main__":
    raise SystemExit(main())
