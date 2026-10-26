"""Run inside a private ACA job to restore a verified prior champion."""

from __future__ import annotations

import argparse
from pathlib import Path

from house_price.azure.evidence import read_json, validate
from house_price.azure.release import rollback_champion
from house_price.catalog.repository import CatalogRepository
from house_price.settings import Settings
from house_price.tracking.mlflow_client import TrackingClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-release", type=Path, required=True)
    args = parser.parse_args()
    previous = read_json(args.previous_release)
    validate(previous, "azure-release.schema.json")
    settings = Settings()  # type: ignore[call-arg]
    repository = CatalogRepository(
        settings.database_url, advisory_lock_id=settings.catalog_advisory_lock_id
    )
    tracking = TrackingClient(
        settings.mlflow_tracking_uri, settings.mlflow_experiment_name,
        recovery_experiment_name=settings.mlflow_recovery_experiment_name,
        registered_model_name=settings.mlflow_registered_model_name,
        champion_alias=settings.mlflow_champion_alias,
    )
    rollback_champion(
        root=settings.model_registry_path, previous_release=previous,
        repository=repository, tracking=tracking,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
