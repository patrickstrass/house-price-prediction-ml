"""Versioned, secret-free operational evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS = Path(__file__).resolve().parents[3] / "specs/003-deploy-azure-containers/contracts"
FORMAT_CHECKER = FormatChecker()
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


@FORMAT_CHECKER.checks("date-time")
def _valid_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True  # The schema's type keyword handles non-string values.
    if not _RFC3339_UTC.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(document: dict[str, Any], schema_name: str) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text())
    Draft202012Validator(schema, format_checker=FORMAT_CHECKER).validate(document)


def write_json(path: Path, document: dict[str, Any], schema_name: str | None = None) -> None:
    if schema_name:
        validate(document, schema_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result
