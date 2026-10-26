"""Ordered manual Container Apps job orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from house_price.azure.evidence import now_utc

JOB_ORDER = ("backup", "mlflow-migrate", "catalog-reconcile", "verify", "trainer", "verify")
TERMINAL = {"Succeeded", "Failed", "Canceled"}


def training_uuid(repository: str, github_run_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"github:{repository}:{github_run_id}"))


def run_sequence(
    *,
    repository: str,
    github_run_id: str,
    start: Callable[[str], str],
    inspect: Callable[[str, str], str],
    wait: Callable[[int], Any] = time.sleep,
    max_polls: int = 360,
    summarize: Callable[[str], dict[str, Any]] | None = None,
    previous_model_version: str | None = None,
    backup_set_id: str | None = None,
    evidence_uri: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "workflow_run_id": github_run_id,
        "training_run_id": training_uuid(repository, github_run_id),
        "started_at": now_utc(),
        "job_executions": [],
        "status": "running",
        "dataset_identity": None,
        "candidate_metrics": [],
        "promotion_decision": None,
        "previous_model_version": previous_model_version,
        "effective_model_version": previous_model_version,
        "backup_set_id": backup_set_id,
        "evidence_uri": evidence_uri,
    }
    for job in JOB_ORDER:
        execution_id = None
        try:
            execution_id = start(job)
            status = "Running"
            for _ in range(max_polls):
                status = inspect(job, execution_id)
                if status in TERMINAL:
                    break
                wait(10)
        except Exception as exc:
            status = "Failed"
            evidence["reason_code"] = f"job_api_{type(exc).__name__.lower()}"
        evidence["job_executions"].append(
            {"job": job, "execution_id": execution_id, "status": status}
        )
        if status != "Succeeded":
            evidence["status"] = "failed"
            evidence["finished_at"] = now_utc()
            return evidence
    if summarize is not None:
        try:
            summary = summarize(evidence["training_run_id"])
            decision = summary["promotion_decision"]
            if decision not in {"promoted", "unchanged", "rejected", "dataset_invalid"}:
                raise ValueError("invalid promotion decision")
            evidence.update(
                dataset_identity=summary.get("dataset_identity"),
                candidate_metrics=summary.get("candidate_metrics", []),
                promotion_decision=decision,
                previous_model_version=summary.get(
                    "previous_model_version", previous_model_version
                ),
                backup_set_id=summary.get("backup_set_id", backup_set_id),
                evidence_uri=summary.get("evidence_uri", evidence_uri),
                effective_model_version=summary.get(
                    "effective_model_version", previous_model_version
                ),
            )
            if not evidence["dataset_identity"] or not evidence["candidate_metrics"]:
                raise ValueError("training summary lacks dataset or candidate metrics")
            if not evidence["backup_set_id"] or not evidence["evidence_uri"]:
                raise ValueError("weekly backup or evidence link is missing")
            if decision != "promoted" and evidence["previous_model_version"] is not None:
                if evidence["effective_model_version"] != evidence["previous_model_version"]:
                    raise ValueError("rejected run changed the champion")
            evidence["status"] = (
                "rejected" if decision in {"rejected", "dataset_invalid"} else "succeeded"
            )
        except (KeyError, TypeError, ValueError):
            evidence["status"] = "failed"
            evidence["reason_code"] = "training_summary_invalid"
    else:
        evidence["status"] = "succeeded"
    evidence["finished_at"] = now_utc()
    return evidence
