#!/usr/bin/env python3
"""Validate that versioned Grafana assets use only the supported SQL interface."""

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

ALLOWED_VIEWS = {
    "observability.grafana_model_metric_history",
    "observability.grafana_candidate_comparison",
    "observability.grafana_training_status",
    "observability.grafana_optuna_convergence",
    "observability.grafana_inference_operations",
}


def sql_queries(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "rawSql" and isinstance(item, str):
                yield item
            yield from sql_queries(item)
    elif isinstance(value, list):
        for item in value:
            yield from sql_queries(item)


def _render_for_validation(query: str) -> str:
    query = re.sub(
        r"\$__timeFilter\(([^)]+)\)",
        r"\1 BETWEEN now() - interval '365 days' AND now()",
        query,
    )
    return query.replace("${family:csv}", "linear,ridge").replace(
        "${training_run_id}", "00000000-0000-4000-8000-000000000001"
    )


def validate_database(queries: list[str], database_url: str) -> None:
    """Execute every rendered query as the read-only Grafana principal."""
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        for query in queries:
            connection.execute(text(_render_for_validation(query))).fetchmany(1)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT * FROM observability.training_runs LIMIT 1"))
    except DBAPIError:
        pass
    else:
        raise ValueError("grafana_reader can read internal observability tables")
    finally:
        engine.dispose()


def validate(root: Path = Path("."), database_url: str | None = None) -> None:
    dashboard = json.loads((root / "grafana/dashboards/house-price-ml.json").read_text())
    alerts = yaml.safe_load((root / "grafana/alerts/house-price-alerts.yaml").read_text())
    queries = [*sql_queries(dashboard), *sql_queries(alerts)]
    if not queries:
        raise ValueError("Grafana assets contain no SQL queries")
    for query in queries:
        relations = set(re.findall(r"(?:FROM|JOIN)\s+([a-zA-Z0-9_.]+)", query, re.I))
        # ``EXTRACT(epoch FROM now())`` contains SQL's FROM keyword but no relation.
        forbidden = relations - ALLOWED_VIEWS - {"now"}
        if forbidden:
            raise ValueError(f"query uses unsupported relations: {sorted(forbidden)}")
        if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", query, re.I):
            raise ValueError("Grafana query is not read-only")
    rules = alerts["groups"][0]["rules"]
    expected = {
        "house-price-weekly-stale",
        "house-price-inference-heartbeat",
        "house-price-quality-gate",
    }
    if {rule["uid"] for rule in rules} != expected:
        raise ValueError("required alert rules are missing")
    heartbeat = next(rule for rule in rules if rule["uid"] == "house-price-inference-heartbeat")
    pending = heartbeat["for"]
    if heartbeat["noDataState"] != "Alerting" or pending not in {"0m", "1m", "2m", "3m"}:
        raise ValueError("heartbeat No Data must alert within five minutes")
    if database_url:
        validate_database(queries, database_url)


if __name__ == "__main__":
    validate(database_url=os.getenv("GRAFANA_DATABASE_URL"))
    print("Grafana dashboard and alert queries are valid")

