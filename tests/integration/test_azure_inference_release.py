import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from house_price.api.app import create_app
from house_price.azure.evidence import write_json
from house_price.azure.release import CANONICAL_PREDICTION, prepare_release
from scripts import azure_release
from tests.contract.test_azure_release import _inputs


@pytest.mark.anyio
async def test_five_local_recreations_keep_champion_without_tracking(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unavailable@127.0.0.1:1/db")
    registry = project_root / "tests/fixtures/model-registry"
    versions = []
    for _ in range(5):
        app = create_app(
            model_registry_path=registry,
            environment_version="azure-preparation",
            heartbeat_seconds=3600,
        )
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://test"
            ) as client:
                ready = await client.get("/health/ready")
                model = await client.get("/v1/model")
                prediction = await client.post("/v1/predictions", json=CANONICAL_PREDICTION)
            assert ready.status_code == model.status_code == prediction.status_code == 200
            assert prediction.json()["model_version"] == model.json()["model_version"]
            assert len(prediction.json()["predictions"]) == 1
            versions.append(model.json()["model_version"])
    assert versions == ["fixture-v1"] * 5


def test_azure_verify_never_routes_before_catalog_and_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    record = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=manifest, target_config_path=target,
    )
    record.update(status="deployed", inference_revision="rev-1")
    release = tmp_path / "release.json"
    write_json(release, record, "azure-release.schema.json")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"status": "partial", "items": []}))
    calls = []
    monkeypatch.setattr(azure_release, "_revision_url", lambda *_args: "https://example.test")
    monkeypatch.setattr(azure_release, "verify_http", lambda _url, value: {
        **value, "status": "verified", "health_result": "passed", "prediction_result": "passed",
    })
    monkeypatch.setattr(azure_release, "az_run", lambda *_args: calls.append(_args))
    monkeypatch.setattr("sys.argv", [
        "azure_release.py", "verify", "--release", str(release),
        "--resource-group", "rg", "--app", "inference", "--catalog-evidence",
        str(catalog), "--execute", "--output", str(tmp_path / "verified.json"),
    ])
    with pytest.raises(ValueError, match="catalog verification"):
        azure_release.main()
    assert calls == []
    failed = json.loads((tmp_path / "verified.json").read_text())
    assert failed["status"] == "failed"
    catalog.write_text(json.dumps({
        "status": "succeeded", "timestamp": datetime.now(UTC).isoformat(),
        "items": [{"model_version": "v1", "status": "verified"}],
    }))
    assert azure_release.main() == 0
    assert len(calls) == 1 and "traffic" in calls[0]
    assert json.loads((tmp_path / "verified.json").read_text())["status"] == "verified"


def test_acr_digest_mismatch_blocks_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(azure_release, "az_json", lambda *_args: {"digest": "sha256:bad"})
    with pytest.raises(ValueError, match="ACR digest mismatch"):
        azure_release._check_acr("example", {
            "inference": {"tag": "example.azurecr.io/inference:sha-abc-run-1",
                          "digest": "sha256:" + "a" * 64},
        })


def test_rollback_requires_previous_revision_prediction_before_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight, storage, manifest, target = _inputs(tmp_path)
    previous = prepare_release(
        preflight_path=preflight, storage_path=storage,
        manifest_path=manifest, target_config_path=target,
    )
    previous.update(
        status="verified", inference_revision="old-revision",
        health_result="passed", prediction_result="passed",
    )
    current = {**previous, "release_id": "00000000-0000-4000-8000-000000000011",
               "previous_release_id": previous["release_id"],
               "status": "deployed", "inference_revision": "new-revision"}
    old_path = tmp_path / "old.json"
    current_path = tmp_path / "current.json"
    write_json(old_path, previous, "azure-release.schema.json")
    write_json(current_path, current, "azure-release.schema.json")
    calls = []
    monkeypatch.setattr(azure_release, "az_run", lambda *args: calls.append(args))
    monkeypatch.setattr(
        azure_release, "_restore_champion_job", lambda *_args: calls.append(("restore",))
    )
    monkeypatch.setattr(azure_release, "_revision_url", lambda *_args: "https://old.test")
    monkeypatch.setattr(
        azure_release, "verify_http",
        lambda *_args: (_ for _ in ()).throw(ValueError("old champion differs")),
    )
    output = tmp_path / "rollback.json"
    monkeypatch.setattr("sys.argv", [
        "azure_release.py", "rollback", "--release", str(current_path),
        "--previous-release", str(old_path), "--resource-group", "rg",
        "--app", "inference", "--execute", "--output", str(output),
    ])
    with pytest.raises(ValueError, match="old champion"):
        azure_release.main()
    assert len(calls) == 2 and calls[0] == ("restore",) and "activate" in calls[1]
    assert json.loads(output.read_text())["rollback_result"] == "failed"
