from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Northstar API"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_api_prefix: str = "/api/v1"
    app_auto_create_schema: bool = True
    app_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: str = "postgresql+asyncpg://northstar:northstar@localhost:5432/northstar"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False
    database_runtime_role: Literal["northstar_app"] = "northstar_app"
    database_apply_runtime_role: bool = True

    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout_seconds: float = Field(default=0.5, gt=0, le=30)
    redis_socket_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672//"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_enabled: bool = False
    background_dispatch_enabled: bool = False

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "northstar-knowledge"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_presign_ttl_seconds: int = 900
    upload_max_bytes: int = 25 * 1024 * 1024
    knowledge_max_extracted_characters: int = Field(default=2_000_000, ge=10_000, le=20_000_000)
    knowledge_max_chunks: int = Field(default=2_000, ge=1, le=20_000)
    knowledge_max_pdf_pages: int = Field(default=500, ge=1, le=5_000)
    knowledge_sitemap_max_urls: int = Field(default=25, ge=1, le=500)
    knowledge_max_docx_uncompressed_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1_048_576,
        le=1_073_741_824,
    )

    jwt_secret: SecretStr = SecretStr("development-only-change-me")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "northstar-api"
    jwt_audience: str = "northstar-web"
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_days: int = 14

    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nvidia_temperature: float = 1.0
    nvidia_top_p: float = 0.95
    nvidia_max_tokens: int = 16_384
    nvidia_enable_thinking: bool = True
    nvidia_embedding_model: str = "nvidia/nemotron-3-embed-1b"
    embedding_dimension: int = 2048
    require_nvidia: bool = False
    allow_deterministic_embeddings: bool = True

    rate_limit_fail_open: bool = True
    login_rate_limit_per_minute: int = Field(default=8, ge=1, le=10_000)
    login_global_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    login_hash_concurrency: int = Field(default=4, ge=1, le=64)
    default_rate_limit_per_minute: int = 30
    agent_global_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    widget_session_rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)
    model_concurrency_per_process: int = Field(default=16, ge=1, le=256)
    maximum_message_characters: int = 12_000
    retrieval_candidate_limit: int = 40
    retrieval_context_limit: int = 8
    retrieval_min_score: float = Field(default=0.20, ge=0, le=1)

    log_level: str = "INFO"
    log_json: bool = True

    seed_admin_email: EmailStr | None = None
    seed_admin_password: SecretStr | None = None
    seed_admin_name: str = "Workspace Owner"
    seed_tenant_name: str = "Northstar Workspace"
    seed_demo_agent: bool = False
    allow_production_seed: bool = False

    @field_validator("seed_admin_email", "seed_admin_password", mode="before")
    @classmethod
    def normalize_optional_seed_credentials(cls, value: object) -> object | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("app_api_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        return "/" + value.strip("/")

    @field_validator("nvidia_temperature")
    @classmethod
    def validate_temperature(cls, value: float) -> float:
        if not 0 <= value <= 2:
            raise ValueError("NVIDIA_TEMPERATURE must be between 0 and 2")
        return value

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        if self.app_env == "production":
            if self.app_auto_create_schema:
                raise ValueError("APP_AUTO_CREATE_SCHEMA must be false in production")
            if (
                len(self.jwt_secret.get_secret_value()) < 32
                or "change-me" in self.jwt_secret.get_secret_value()
                or "replace-" in self.jwt_secret.get_secret_value()
            ):
                raise ValueError("JWT_SECRET must be a strong secret in production")
            if not self.require_nvidia or not self.nvidia_api_key:
                raise ValueError("Production requires REQUIRE_NVIDIA=true and NVIDIA_API_KEY")
            if self.allow_deterministic_embeddings:
                raise ValueError("ALLOW_DETERMINISTIC_EMBEDDINGS must be false in production")
            if self.rate_limit_fail_open:
                raise ValueError("RATE_LIMIT_FAIL_OPEN must be false in production")
            if not self.s3_configured:
                raise ValueError("Production requires configured object storage")
            if any(origin == "*" for origin in self.app_cors_origins):
                raise ValueError("Wildcard APP_CORS_ORIGINS is not permitted in production")
            unsafe_connections = (
                self.database_url,
                self.redis_url,
                self.rabbitmq_url,
                self.s3_secret_access_key.get_secret_value() if self.s3_secret_access_key else "",
            )
            if any("replace-" in value or "change-me" in value for value in unsafe_connections):
                raise ValueError("Production connection credentials still contain placeholders")
            if self.seed_admin_password:
                if not self.allow_production_seed:
                    raise ValueError(
                        "Production seed credentials require the explicit ALLOW_PRODUCTION_SEED flag"
                    )
                seed_password = self.seed_admin_password.get_secret_value()
                if len(seed_password) < 12 or "change-me" in seed_password:
                    raise ValueError("Production seed administrator password is not strong enough")
        if bool(self.seed_admin_email) != bool(self.seed_admin_password):
            raise ValueError("SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD must be provided together")
        if bool(self.s3_access_key_id) != bool(self.s3_secret_access_key):
            raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be provided together")
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_access_key_id and self.s3_secret_access_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
