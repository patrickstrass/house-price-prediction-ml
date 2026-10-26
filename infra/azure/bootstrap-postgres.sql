\set ON_ERROR_STOP on
\getenv migration_password MIGRATION_PASSWORD
\getenv pipeline_password PIPELINE_PASSWORD
\getenv grafana_password GRAFANA_PASSWORD

SELECT format('CREATE ROLE migration_user LOGIN PASSWORD %L', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migration_user') \gexec
SELECT format('CREATE ROLE pipeline_writer LOGIN PASSWORD %L', :'pipeline_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeline_writer') \gexec
SELECT format('CREATE ROLE grafana_reader LOGIN PASSWORD %L', :'grafana_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') \gexec

\connect house_price
CREATE SCHEMA IF NOT EXISTS observability AUTHORIZATION migration_user;
CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION migration_user;
REVOKE ALL ON SCHEMA observability FROM PUBLIC;
REVOKE ALL ON SCHEMA catalog FROM PUBLIC;
GRANT CONNECT ON DATABASE house_price TO migration_user, pipeline_writer, grafana_reader;
GRANT USAGE ON SCHEMA observability TO pipeline_writer, grafana_reader;
GRANT USAGE ON SCHEMA catalog TO pipeline_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA observability TO grafana_reader;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA observability TO pipeline_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA catalog TO pipeline_writer;

\connect optuna
GRANT CONNECT, CREATE ON DATABASE optuna TO pipeline_writer;

\connect mlflow
GRANT CONNECT ON DATABASE mlflow TO migration_user;
