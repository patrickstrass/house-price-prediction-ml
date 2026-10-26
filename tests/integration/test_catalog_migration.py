import os
import subprocess
import sys
import uuid
from importlib import import_module

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

catalog_migration = import_module("migrations.versions.003_model_catalog")


def test_catalog_upgrade_and_downgrade_are_present_in_offline_migration() -> None:
    environment = {
        **os.environ,
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://migration_user:test@localhost/db",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "REVOKE ALL ON SCHEMA catalog FROM PUBLIC" in result.stdout
    assert "operation_events_append_only" in result.stdout
    assert "catalog_one_champion" in result.stdout
    assert "DROP TABLE IF EXISTS catalog.deletion_requests" in catalog_migration.DOWN_SQL


@pytest.mark.skipif(
    not os.getenv("CATALOG_MIGRATION_TEST_DATABASE_URL"),
    reason="set CATALOG_MIGRATION_TEST_DATABASE_URL for PostgreSQL migration integration",
)
def test_catalog_constraints_append_only_trigger_and_role_permissions() -> None:
    schema = f"catalog_test_{uuid.uuid4().hex[:12]}"
    database_url = os.environ["CATALOG_MIGRATION_TEST_DATABASE_URL"]
    engine = create_engine(database_url)
    sql = catalog_migration.UP_SQL.replace("catalog", schema)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE SCHEMA {schema} AUTHORIZATION migration_user"
        )
        connection.exec_driver_sql(sql)
        assert connection.execute(
            text("SELECT has_schema_privilege('pipeline_writer', :schema, 'USAGE')"),
            {"schema": schema},
        ).scalar_one()
        connection.execute(
            text(
                f"INSERT INTO {schema}.operation_events "
                "(event_id, operation_id, operation, phase, result, occurred_at) "
                "VALUES (:event, :operation, 'reconcile', 'test', 'started', now())"
            ),
            {"event": uuid.uuid4(), "operation": uuid.uuid4()},
        )
        with pytest.raises(DBAPIError, match="append-only"):
            with connection.begin_nested():
                connection.execute(text(f"DELETE FROM {schema}.operation_events"))
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        f"INSERT INTO {schema}.model_entries "
                        "(model_version, manifest_schema_version, manifest_sha256, "
                        "model_sha256, package_relpath, training_run_id, integrity_status, "
                        "catalog_status, lifecycle_status, promotion_status, first_seen_at) "
                        "VALUES ('bad', '2.0.0', :upper_hash, :hash, 'models/bad', "
                        ":run, 'verified', 'registered', 'active', 'candidate', now())"
                    ),
                    {
                        "upper_hash": "A" * 64,
                        "hash": "b" * 64,
                        "run": uuid.uuid4(),
                    },
                )
        connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
