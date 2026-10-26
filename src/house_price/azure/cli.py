"""Safe subprocess adapter; callers only invoke it after explicit execution."""

from __future__ import annotations

import json
import subprocess
from typing import Any


def az_json(*arguments: str) -> Any:
    completed = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def az_run(*arguments: str) -> None:
    subprocess.run(["az", *arguments, "--only-show-errors"], check=True, timeout=1800)
