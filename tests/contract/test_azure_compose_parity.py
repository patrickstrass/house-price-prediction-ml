from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_all_compose_services_have_azure_destination() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    matrix = (ROOT / "specs/003-deploy-azure-containers/compose-feasibility.md").read_text()
    for service in compose["services"]:
        assert f"`{service}`" in matrix


def test_all_compose_resources_have_azure_destination_and_acceptance() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    matrix = (ROOT / "specs/003-deploy-azure-containers/compose-feasibility.md").read_text()
    for volume in compose["volumes"]:
        assert f"`{volume}`" in matrix
    for construct in (
        "depends_on", "build", "volumes", "ports", "environment", "healthcheck",
        "weekly-output", "Agenda semanal", "Key Vault", "Aceite antes do deploy",
    ):
        assert construct in matrix
