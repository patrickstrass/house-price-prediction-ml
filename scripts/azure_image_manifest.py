"""Record locked ACR tag digests after an explicitly enabled CI publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from house_price.azure.cli import az_json
from house_price.azure.evidence import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--git-revision", required=True)
    parser.add_argument("--ci-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    images = {}
    for service in ("trainer", "inference", "mlflow"):
        item = az_json(
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            args.registry,
            "--name",
            f"{service}:{args.tag}",
        )
        digest = item.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError(f"missing digest for {service}")
        images[service] = {
            "tag": f"{args.registry}.azurecr.io/{service}:{args.tag}",
            "digest": digest,
            "ci_approved": True,
        }
    manifest = {"git_revision": args.git_revision, "ci_run_id": args.ci_run_id, "images": images}
    write_json(args.output, manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
