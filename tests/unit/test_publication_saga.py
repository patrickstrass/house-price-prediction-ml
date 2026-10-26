import shutil
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import mlflow.pyfunc
import numpy as np
import pytest
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.tree import DecisionTreeRegressor

from house_price.catalog.models import PromotionStatus
from house_price.data.validation import DatasetSnapshot
from house_price.domain import CandidateFamily, ModelCandidate, ModelMetrics
from house_price.registry.manifest import PersistedModelManifestV2, validate_package
from house_price.registry.promotion import PublicationSaga
from house_price.training.publishing import build_model_package


class FakeRepository:
    def __init__(self) -> None:
        self.entries = {}
        self.events = []

    @contextmanager
    def mutation_lock(self):
        yield object()

    def get_model_entry(self, model_version, *, connection=None):
        return self.entries.get(model_version)

    def list_model_entries(self, *, connection=None):
        return list(self.entries.values())

    def upsert_model_entry(self, entry, *, connection=None):
        self.entries[entry.model_version] = entry
        return entry

    def update_model_entry(self, model_version, expected_revision, changes, *, connection=None):
        entry = self.entries[model_version].model_copy(update=changes)
        self.entries[model_version] = entry
        return entry

    def append_event(self, event, *, connection=None):
        self.events.append(event)


class FakeTracking:
    def __init__(self) -> None:
        self.current = "previous"
        self.restored = []

    def log_artifact_to_run(self, *_args, **_kwargs):
        return None

    def register_model_version(self, **_kwargs):
        return "2"

    def set_run_tags(self, *_args, **_kwargs):
        return None

    def set_model_version_tags(self, *_args, **_kwargs):
        return None

    def get_champion_version(self):
        return self.current

    def set_champion(self, version):
        self.current = version

    def restore_champion(self, version):
        self.restored.append(version)
        self.current = version


@pytest.mark.parametrize(
    "family,estimator_class",
    [
        (CandidateFamily.LINEAR, LinearRegression),
        (CandidateFamily.DECISION_TREE, DecisionTreeRegressor),
        (CandidateFamily.RANDOM_FOREST, RandomForestRegressor),
    ],
)
def test_builder_creates_self_contained_v2_package(
    tmp_path: Path, family: CandidateFamily, estimator_class
) -> None:
    features = np.arange(32, dtype=float).reshape(4, 8)
    estimator = estimator_class().fit(features, np.arange(4))
    snapshot = DatasetSnapshot(
        dataset_name="california_housing",
        dataset_version="fixture-v1",
        source_uri="fixture",
        sha256="a" * 64,
        row_count=20640,
        feature_names=(
            "MedInc",
            "HouseAge",
            "AveRooms",
            "AveBedrms",
            "Population",
            "AveOccup",
            "Latitude",
            "Longitude",
        ),
        target_name="MedHouseVal",
        created_at=datetime.now(UTC),
        schema_version="1.0.0",
    )
    candidate = ModelCandidate(
        training_run_id="00000000-0000-4000-8000-000000000001",
        family=family,
        mlflow_run_id="run-1",
        best_trial_number=0,
        parameters={},
        metrics=ModelMetrics(mae=0.1, mse=0.01, rmse=0.1, r2=0.9),
    )
    result = build_model_package(
        root=tmp_path,
        winner=candidate,
        estimator=estimator,
        plots=[],
        snapshot=snapshot,
        settings=SimpleNamespace(
            code_revision="abc123",
            training_environment_version="env-v1",
            mlflow_registered_model_name="house-price-regressor",
        ),
        split_seed=42,
        test_fraction=0.33,
    )
    manifest = validate_package(result.path, require_directory_identity=True)
    assert isinstance(manifest, PersistedModelManifestV2)
    assert manifest.artifacts.checksums
    loaded = mlflow.pyfunc.load_model(str(result.path / "model"))
    assert len(loaded.predict(features[:1])) == 1


@pytest.mark.parametrize(
    "failure_phase",
    [
        "intent_persisted",
        "artifacts_uploaded",
        "model_version_created",
        "package_materialized",
        "alias_prepared",
        "pointer_committed",
        "approved",
    ],
)
def test_publication_faults_are_resumable_or_compensated(
    project_root: Path, tmp_path: Path, failure_phase: str
) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    package = tmp_path / "fixture-v1"
    shutil.copytree(project_root / "tests/fixtures/model-registry/models/fixture-v1", package)
    repository = FakeRepository()
    tracking = FakeTracking()

    def inject(phase: str) -> None:
        if phase == failure_phase:
            raise OSError(f"fault:{phase}")

    saga = PublicationSaga(root, tracking, repository, phase_hook=inject)
    with pytest.raises(OSError, match="fault"):
        saga.publish(package, model_uri="runs:/fixture-mlflow-run/model")

    pointer_committed = failure_phase in {"pointer_committed", "approved"}
    assert (root / "current.json").exists() is pointer_committed
    if not pointer_committed:
        assert tracking.current == "previous"
    assert repository.events[-1].result in {"failed", "compensated"}


def test_second_publication_supersedes_previous_champion(
    project_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    package = tmp_path / "fixture-v1"
    shutil.copytree(project_root / "tests/fixtures/model-registry/models/fixture-v1", package)
    repository = FakeRepository()
    tracking = FakeTracking()
    saga = PublicationSaga(root, tracking, repository)
    manifest = validate_package(package)
    old = saga._entry(manifest, "a" * 64).model_copy(update={
        "model_version": "previous-v1", "promotion_status": PromotionStatus.CHAMPION,
    })
    repository.entries[old.model_version] = old
    model_version, promoted = saga.publish(package, model_uri="runs:/fixture-mlflow-run/model")
    assert promoted is True
    assert repository.entries["previous-v1"].promotion_status is PromotionStatus.SUPERSEDED
    assert repository.entries[model_version].promotion_status is PromotionStatus.CHAMPION
