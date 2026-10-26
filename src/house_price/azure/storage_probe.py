"""Filesystem semantics probe; run inside the target mount before future promotion."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from house_price.azure.evidence import now_utc

CONFIG_FIELDS = ("protocol", "registry_mount", "artifact_mount", "uid_rw", "uid_ro", "runtime")


def configuration_sha256(configuration: dict) -> str:
    if set(configuration) != set(CONFIG_FIELDS):
        raise ValueError("storage configuration needs protocol, mounts, UIDs and runtime")
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def probe_mount(
    registry: Path, artifacts: Path, *, expected_uid: int = 10001, artifact_uid: int = 10002
) -> dict:
    checks: dict[str, bool] = {}
    checks["distinct_stores"] = registry.resolve() != artifacts.resolve()
    checks["owner_uid"] = registry.stat().st_uid == expected_uid
    checks["artifact_owner_uid"] = artifacts.stat().st_uid == artifact_uid
    checks["writable"] = os.access(registry, os.W_OK)
    work = registry / f".azure-probe-{uuid4()}"
    try:
        work.mkdir()
    except OSError:
        checks["filesystem_ops"] = False
        return {"checked_at": now_utc(), "checks": checks, "source": "local", "status": "failed"}
    try:
        source = work / "candidate"
        destination = work / "committed"
        with source.open("wb") as output:
            output.write(b"azure-storage-probe")
            output.flush()
            os.fsync(output.fileno())
        checks["same_filesystem"] = source.stat().st_dev == work.stat().st_dev
        os.replace(source, destination)
        checks["atomic_replace"] = destination.read_bytes() == b"azure-storage-probe"
        descriptor = os.open(work, os.O_RDONLY)
        try:
            os.fsync(descriptor)
            checks["directory_fsync"] = True
        finally:
            os.close(descriptor)
        try:
            collision = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            checks["immutable_collision"] = True
        else:
            os.close(collision)
            checks["immutable_collision"] = False
        with ThreadPoolExecutor(max_workers=4) as readers:
            values = list(readers.map(lambda _: destination.read_bytes(), range(16)))
        checks["concurrent_readers"] = all(value == b"azure-storage-probe" for value in values)
        incomplete = work / "incomplete"
        incomplete.write_bytes(b"partial")
        checks["injected_failure"] = (
            destination.read_bytes() == b"azure-storage-probe" and incomplete.exists()
        )
    except OSError:
        checks["filesystem_ops"] = False
    finally:
        if work.exists():
            for child in work.iterdir():
                child.unlink()
            work.rmdir()
    return {
        "checked_at": now_utc(),
        "checks": checks,
        "source": "aca" if os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME") else "local",
        "execution_name": os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME"),
        "status": "passed" if all(checks.values()) else "failed",
    }


def probe_readonly(registry: Path, *, expected_uid: int = 10001) -> dict:
    denied = False
    candidate = registry / f".ro-probe-{uuid4()}"
    try:
        with candidate.open("xb") as output:
            output.write(b"must-not-write")
    except OSError:
        denied = True
    finally:
        candidate.unlink(missing_ok=True)
    return {
        "checked_at": now_utc(),
        "uid": os.geteuid(),
        "owner_uid": registry.stat().st_uid,
        "readonly_inference": denied,
        "source": "aca" if os.environ.get("CONTAINER_APP_REPLICA_NAME") else "local",
        "replica_name": os.environ.get("CONTAINER_APP_REPLICA_NAME"),
        "status": "passed" if denied and os.geteuid() == expected_uid else "failed",
    }


def finalize_probe(basic: dict, readonly: dict, observations: list[dict]) -> dict:
    required = (
        "distinct_stores",
        "owner_uid",
        "artifact_owner_uid",
        "writable",
        "same_filesystem",
        "atomic_replace",
        "directory_fsync",
        "immutable_collision",
        "concurrent_readers",
        "injected_failure",
    )
    checks = dict(basic["checks"])
    checks["aca_provenance"] = basic.get("source") == readonly.get("source") == "aca" and all(
        item.get("source") == "aca" for item in observations
    )
    checks["readonly_inference"] = (
        readonly.get("readonly_inference") is True and readonly.get("status") == "passed"
    )
    if len(observations) == 5:
        identities = {
            (item.get("model_version"), item.get("pointer_sha256")) for item in observations
        }
        recreation_ids = {item.get("recreation_id") for item in observations}
        checks["five_recreations"] = (
            len(identities) == 1 and len(recreation_ids) == 5 and None not in recreation_ids
        )
    else:
        checks["five_recreations"] = False
    result = dict(basic)
    result["checks"] = checks
    result["checked_at"] = now_utc()
    result["status"] = (
        "passed"
        if all(
            checks.get(name) is True
            for name in (*required, "readonly_inference", "five_recreations", "aca_provenance")
        )
        else "failed"
    )
    return result
