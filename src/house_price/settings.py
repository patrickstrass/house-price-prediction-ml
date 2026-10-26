"""Environment-only configuration for training and inference."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    database_url: str = Field(min_length=1)
    optuna_storage_url: str = Field(min_length=1)
    mlflow_tracking_uri: str = Field(min_length=1)
    mlflow_experiment_name: str = Field(default="house-price-training-v2", min_length=1)
    mlflow_recovery_experiment_name: str = Field(
        default="house-price-model-reconciliation", min_length=1
    )
    mlflow_registered_model_name: str = Field(default="house-price-regressor", min_length=1)
    mlflow_champion_alias: str = Field(default="champion", min_length=1)
    catalog_advisory_lock_id: int = 0x485052494345
    postgres_volume_name: str = Field(default="house-price-postgres-data", min_length=1)
    mlflow_artifacts_volume_name: str = Field(
        default="house-price-mlflow-artifacts", min_length=1
    )
    model_registry_volume_name: str = Field(default="house-price-model-registry", min_length=1)
    model_registry_path: Path = Path("/model-registry")
    dataset_path: Path = Path("/app/data/california_housing.parquet")
    dataset_manifest_path: Path = Path("/app/data/dataset-manifest.json")
    environment_version: str = Field(default="local-dev", min_length=1)
    training_environment_version: str = Field(default="local-dev", min_length=1)
    code_revision: str = Field(default="local-dev", min_length=1)
    log_level: str = "INFO"
    pipeline_password: SecretStr | None = None
    azure_deployment: bool = False

    @model_validator(mode="after")
    def require_azure_tls(self) -> "Settings":
        if self.azure_deployment:
            for value in (self.database_url, self.optuna_storage_url):
                if "sslmode=verify-full" not in value:
                    raise ValueError("Azure PostgreSQL URLs require sslmode=verify-full")
        return self

    @field_validator("database_url", "optuna_storage_url")
    @classmethod
    def reject_unsafe_database_url(cls, value: str) -> str:
        if "changeme" in value.lower():
            raise ValueError("database URLs must not contain placeholder credentials")
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("only PostgreSQL URLs are supported")
        return value

    @field_validator("mlflow_tracking_uri")
    @classmethod
    def validate_tracking_uri(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("MLflow tracking URI must use HTTP(S)")
        return value.rstrip("/")

    @field_validator(
        "mlflow_experiment_name",
        "mlflow_recovery_experiment_name",
        "mlflow_registered_model_name",
        "mlflow_champion_alias",
        "postgres_volume_name",
        "mlflow_artifacts_volume_name",
        "model_registry_volume_name",
    )
    @classmethod
    def reject_secret_like_identifiers(cls, value: str) -> str:
        lowered = value.lower()
        if any(token in lowered for token in ("password=", "token=", "secret=", "://", "@")):
            raise ValueError("catalog identifiers must not contain credentials or URLs")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
