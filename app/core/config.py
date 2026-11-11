"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (two levels up from this file: app/core/config.py -> app -> backend)
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SECRET_KEY = "change-me-in-production"
DEFAULT_SEED_PASSWORD = "Password123!"
LOCAL_ENVS = {"development", "test"}
UNSAFE_SECRET_KEYS = {
    DEFAULT_SECRET_KEY,
    "change-me-in-production-please-use-a-long-random-string",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim"
    APP_ENV: str = "development"

    # Auth / JWT
    SECRET_KEY: str = DEFAULT_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS — comma-separated list of allowed origins (kept as a plain string so
    # pydantic-settings does not attempt to JSON-decode it from the .env file).
    CORS_ORIGINS: str = "http://localhost:3000"

    # Behaviour
    AUTO_SEED: bool = True
    # Password for every seeded account. Override it on any shared server.
    SEED_PASSWORD: str = DEFAULT_SEED_PASSWORD

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    def validate_runtime_safety(self) -> None:
        # Shared servers (e.g. APP_ENV=demo) must not expose the documented
        # seed password or the default JWT secret.
        if self.APP_ENV.lower() not in LOCAL_ENVS:
            if self.SECRET_KEY in UNSAFE_SECRET_KEYS:
                raise RuntimeError(f"APP_ENV={self.APP_ENV} requires a random SECRET_KEY.")
            if self.AUTO_SEED and self.SEED_PASSWORD == DEFAULT_SEED_PASSWORD:
                raise RuntimeError(f"APP_ENV={self.APP_ENV} requires a non-default SEED_PASSWORD.")
        if not self.is_production:
            return
        if self.SECRET_KEY in UNSAFE_SECRET_KEYS:
            raise RuntimeError("Production requires a random SECRET_KEY.")
        if self.AUTO_SEED:
            raise RuntimeError("Production requires AUTO_SEED=false.")


settings = Settings()
