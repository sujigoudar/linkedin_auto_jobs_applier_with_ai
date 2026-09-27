"""Commercial-service configuration.

Deliberately separate from `signal-copier/app/config.py` -- this is a
different process with a different database (Postgres, not the owner's
SQLite execution store) and different secrets. Nothing here can read or
override the owner's execution-service configuration.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=True, extra="ignore")

    #: A real Postgres DSN in every real environment. Tests never read this
    #: -- tests/conftest.py's `postgres_url` fixture spins up and tears down
    #: its own disposable cluster and passes that URL directly, so a
    #: missing/placeholder value here never accidentally points a test at
    #: a real database.
    COMMERCIAL_DATABASE_URL: str = "postgresql+psycopg://commercial:commercial@localhost:5432/commercial"

    #: LOCAL_SIM | INTEGRATION_ISOLATED | PLATFORM_DEMO | PRIVATE_SHADOW |
    #: COMMERCIAL_LIVE -- see spec/docs/02_architecture_and_tenancy.md's
    #: "Environments" section. Defaults to the safest, most restrictive
    #: value; COMMERCIAL_LIVE is never the default anywhere.
    ENVIRONMENT: str = "LOCAL_SIM"


_settings = _Settings()

COMMERCIAL_DATABASE_URL = _settings.COMMERCIAL_DATABASE_URL
ENVIRONMENT = _settings.ENVIRONMENT
