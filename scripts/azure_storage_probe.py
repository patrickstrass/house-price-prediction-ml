"""Probe mounted filesystems; no Azure provisioning occurs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from house_price.azure.evidence import write_json
from house_price.azure.storage_probe import configuration_sha256, probe_mount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-id", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--registry-storage-id", required=True)
    parser.add_argument("--artifact-storage-id", required=True)
    parser.add_argument("--registry-path", type=Path, required=True)
    parser.add_argument("--artifact-path", type=Path, required=True)
    parser.add_argument("--expected-uid", type=int, default=10001)
    parser.add_argument("--artifact-uid", type=int, default=10002)
    parser.add_argument("--readonly-uid", type=int, default=10001)
    parser.add_argument("--protocol", default="SMB")
    parser.add_argument("--runtime", required=True, help="Immutable ACA runtime image digest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe_mount(
        args.registry_path, args.artifact_path,
        expected_uid=args.expected_uid, artifact_uid=args.artifact_uid,
    )
    configuration = {
        "protocol": args.protocol,
        "registry_mount": str(args.registry_path),
        "artifact_mount": str(args.artifact_path),
        "uid_rw": args.expected_uid,
        "uid_ro": args.readonly_uid,
        "runtime": args.runtime,
    }
    result.update(
        {
            "probe_id": str(uuid4()),
            "preflight_id": args.preflight_id,
            "environment_id": args.environment_id,
            "registry_storage_id": args.registry_storage_id,
            "artifact_storage_id": args.artifact_storage_id,
            "configuration": configuration,
            "configuration_sha256": configuration_sha256(configuration),
        }
    )
    # This preliminary probe is intentionally insufficient for release approval.
    # ACA recreation, concurrent readers, fault injection, and RO mount need separate evidence.
    result["status"] = "pending" if result["status"] == "passed" else "failed"
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pending" else 3


if __name__ == "__main__":
    raise SystemExit(main())
