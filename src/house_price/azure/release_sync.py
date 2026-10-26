"""Persist a verified release record inside a private ACA backup share."""

from __future__ import annotations

import json
import os
from pathlib import Path

from house_price.azure.evidence import validate, write_json


def main() -> int:
    payload = os.environ["RELEASE_JSON"]
    record = json.loads(payload)
    validate(record, "azure-release.schema.json")
    if record["status"] != "verified":
        raise ValueError("only a verified release can become the current release")
    archive = Path("/backup/releases") / f"{record['release_id']}.json"
    if archive.exists() and json.loads(archive.read_text()) != record:
        raise ValueError("release archive identity collision")
    if not archive.exists():
        write_json(archive, record, "azure-release.schema.json")
    write_json(Path("/backup/current-release.json"), record, "azure-release.schema.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
