import json
from pathlib import Path
from uuid import uuid4

import pytest

from house_price.azure.jobs import JOB_ORDER, run_sequence, training_uuid
from scripts import azure_jobs, create_weekly_evidence
from scripts.azure_jobs import job_start_command, parse_job_event

ROOT = Path(__file__).resolve().parents[2]


def test_job_failure_blocks_trainer() -> None:
    started = []

    def start(job: str) -> str:
        started.append(job)
        return job

    result = run_sequence(
        repository="org/repo",
        github_run_id="123",
        start=start,
        inspect=lambda job, _id: "Failed" if job == "catalog-reconcile" else "Succeeded",
        wait=lambda _: None,
    )
    assert result["status"] == "failed"
    assert started == ["backup", "mlflow-migrate", "catalog-reconcile"]


def test_successful_order_and_idempotent_id() -> None:
    result = run_sequence(
        repository="org/repo",
        github_run_id="123",
        start=lambda job: job,
        inspect=lambda _job, _id: "Succeeded",
        wait=lambda _: None,
    )
    assert [x["job"] for x in result["job_executions"]] == list(JOB_ORDER)
    assert result["status"] == "succeeded"
    second = run_sequence(
        repository="org/repo", github_run_id="123", start=lambda job: job,
        inspect=lambda _job, _id: "Succeeded", wait=lambda _: None,
    )
    assert result["training_run_id"] == second["training_run_id"]


def test_trainer_receives_stable_id_without_overriding_other_jobs() -> None:
    options = dict(prefix="hp", resource_group="rg", subscription="sub",
                   repository="org/repo", github_run_id="123")
    trainer = job_start_command("trainer", **options)
    assert trainer[-3:] == ["--args", "--run-id", training_uuid("org/repo", "123")]
    assert "--args" not in job_start_command("backup", **options)


def test_azure_workflow_is_inert_until_enabled() -> None:
    workflow = (ROOT / ".github/workflows/azure-weekly.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "AZURE_DEPLOYMENT_ENABLED == 'true'" in workflow
    assert "schedule:" not in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow


def test_api_exception_still_produces_failure_evidence() -> None:
    def fail(_job: str) -> str:
        raise OSError("credential details should not be copied")

    result = run_sequence(
        repository="org/repo",
        github_run_id="123",
        start=fail,
        inspect=lambda _job, _id: "Succeeded",
        wait=lambda _: None,
    )
    assert result["status"] == "failed"
    assert result["job_executions"][0]["status"] == "Failed"
    assert "credential details" not in str(result)


def test_rejected_candidate_keeps_previous_champion_and_evidence() -> None:
    result = run_sequence(
        repository="org/repo", github_run_id="rejected",
        start=lambda job: f"id-{job}", inspect=lambda _job, _id: "Succeeded",
        wait=lambda _: None, previous_model_version="v1", backup_set_id="backup-1",
        evidence_uri="file:///evidence/rejected.json",
        summarize=lambda _id: {
            "dataset_identity": {"sha256": "a" * 64},
            "candidate_metrics": [{"family": "linear", "rmse": 1.0}],
            "promotion_decision": "rejected", "effective_model_version": "v1",
        },
    )
    assert result["status"] == "rejected"
    assert result["previous_model_version"] == result["effective_model_version"] == "v1"
    assert result["backup_set_id"] == "backup-1"
    assert result["evidence_uri"].endswith("rejected.json")


def test_invalid_dataset_cannot_change_champion() -> None:
    result = run_sequence(
        repository="org/repo", github_run_id="invalid",
        start=lambda job: job, inspect=lambda _job, _id: "Succeeded",
        wait=lambda _: None, previous_model_version="v1",
        summarize=lambda _id: {
            "promotion_decision": "dataset_invalid", "effective_model_version": "v2",
        },
    )
    assert result["status"] == "failed"
    assert result["reason_code"] == "training_summary_invalid"


def test_failure_at_each_prerequisite_blocks_trainer() -> None:
    for failed in JOB_ORDER[:4]:
        started = []

        result = run_sequence(
            repository="org/repo", github_run_id=failed,
            start=lambda job, started=started: started.append(job) or job,
            inspect=lambda job, _id, failed=failed: (
                "Failed" if job == failed else "Succeeded"
            ),
            wait=lambda _: None,
        )
        assert result["status"] == "failed"
        assert "trainer" not in started


def test_weekly_evidence_links_azure_result_even_on_failure(tmp_path: Path, monkeypatch) -> None:
    result = tmp_path / "result.json"
    run_id = str(uuid4())
    result.write_text(json.dumps({
        "status": "failed", "training_run_id": run_id,
        "previous_model_version": "v1", "effective_model_version": "v1",
        "job_executions": [{"job": "backup", "execution_id": "job-1", "status": "Failed"}],
    }))
    output = tmp_path / "weekly-evidence.json"
    monkeypatch.setattr("sys.argv", [
        "create_weekly_evidence.py", "--workflow-run-id", "123",
        "--azure-result", str(result), "--output", str(output),
    ])
    create_weekly_evidence.main()
    evidence = json.loads(output.read_text())
    assert evidence["training_run_id"] == run_id
    assert evidence["effective_model_version"] == "v1"
    assert "result.json" in evidence["artifact_checksums"]


def test_job_log_parser_requires_exact_terminal_event_and_training_id() -> None:
    run_id = str(uuid4())
    event = {"event": "training_completed", "training_run_id": run_id,
             "promotion_decision": "rejected"}
    wrapped = json.dumps({"Log": json.dumps(event)})
    assert parse_job_event(wrapped, "training_completed", training_run_id=run_id) == event
    with pytest.raises(ValueError):
        parse_job_event(wrapped, "training_completed", training_run_id=str(uuid4()))


def test_mlflow_writer_pause_waits_for_stopped_state(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(azure_jobs, "az_run", lambda *args: calls.append(args))
    monkeypatch.setattr(azure_jobs, "az_json", lambda *_args: {
        "properties": {"runningStatus": "Stopped"}
    })
    azure_jobs.set_mlflow_running(
        subscription="sub", group="rg", prefix="hprice", running=False
    )
    assert len(calls) == 1
    assert "/hprice-mlflow/stop?" in calls[0][4]
