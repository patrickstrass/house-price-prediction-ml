"""Compare a saved Azure Cost Management export with the approved preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from house_price.azure.cost import compare_cost
from house_price.azure.evidence import read_json, validate, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    preflight = read_json(args.preflight)
    validate(preflight, "azure-preflight.schema.json")
    result = compare_cost(preflight, read_json(args.observed))
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "within_limit" else 3


if __name__ == "__main__":
    raise SystemExit(main())
