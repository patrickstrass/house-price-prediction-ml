"""Collect RO/recreation evidence and finalize a future real ACA storage probe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from house_price.azure.evidence import read_json, sha256_file, write_json
from house_price.azure.storage_probe import finalize_probe, probe_readonly


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ro = sub.add_parser("readonly")
    ro.add_argument("--registry-path", type=Path, required=True)
    ro.add_argument("--expected-uid", type=int, default=10001)
    ro.add_argument("--output", type=Path, required=True)
    observe = sub.add_parser("observe")
    observe.add_argument("--registry-path", type=Path, required=True)
    observe.add_argument("--recreation-id")
    observe.add_argument("--output", type=Path, required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--basic", type=Path, required=True)
    final.add_argument("--readonly", type=Path, required=True)
    final.add_argument("--observations", nargs=5, type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "readonly":
        result = probe_readonly(args.registry_path, expected_uid=args.expected_uid)
    elif args.command == "observe":
        pointer = args.registry_path / "current.json"
        contents = read_json(pointer)
        result = {
            "recreation_id": args.recreation_id or os.environ.get("CONTAINER_APP_REPLICA_NAME"),
            "model_version": contents.get("model_version"),
            "pointer_sha256": sha256_file(pointer),
            "source": "aca" if os.environ.get("CONTAINER_APP_REPLICA_NAME") else "local",
        }
    else:
        result = finalize_probe(
            read_json(args.basic),
            read_json(args.readonly),
            [read_json(path) for path in args.observations],
        )
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status", "passed") == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
