"""Application configuration settings for the EMS backend service."""

import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Application settings loaded from environment variables with safe defaults."""

    # Project metadata
    PROJECT_NAME: str = "Greek Commercial Behind-the-Meter EMS"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"

    # Runtime environment
    ENVIRONMENT: str = Field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "development")
    )
    DEBUG: bool = Field(
        default_factory=lambda: os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")
    )

    # Security and Access Control
    API_KEY: str | None = Field(
        default_factory=lambda: os.getenv("API_KEY", None)
    )
    # Explicit opt-in for keyless access; honoured only when ENVIRONMENT == "development".
    ALLOW_OPEN_DEV_ACCESS: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_OPEN_DEV_ACCESS", "false").lower() in ("true", "1", "yes")
    )
    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if origin.strip()
        ]
        if os.getenv("ENVIRONMENT") == "production"
        else ["*"]
    )

    # Database configuration
    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./ems.db")
    )
    SQLITE_DB_PATH: str = Field(
        default_factory=lambda: os.getenv("SQLITE_DB_PATH", "ems.db")
    )

    # Telegram bot configuration
    TELEGRAM_BOT_TOKEN: str = Field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    TELEGRAM_DEFAULT_CHAT_ID: int | None = Field(
        default_factory=lambda: int(os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "0"))
        if os.getenv("TELEGRAM_DEFAULT_CHAT_ID")
        else None
    )

    # Viber bot configuration
    VIBER_BOT_TOKEN: str = Field(
        default_factory=lambda: str(os.getenv("VIBER_BOT_TOKEN") or os.getenv("VIBER_AUTH_TOKEN") or "")
    )
    VIBER_AUTH_TOKEN: str = Field(
        default_factory=lambda: str(os.getenv("VIBER_AUTH_TOKEN") or os.getenv("VIBER_BOT_TOKEN") or "")
    )
    VIBER_WEBHOOK_URL: str = Field(
        default_factory=lambda: os.getenv("VIBER_WEBHOOK_URL", "")
    )

    # Alert throttling & hysteresis parameters
    ALERT_COOLDOWN_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))
    )
    ALERT_HYSTERESIS_FACTOR: float = Field(
        default_factory=lambda: float(os.getenv("ALERT_HYSTERESIS_FACTOR", "0.90"))
    )
    ALERT_DEBOUNCE_SAMPLES: int = Field(
        default_factory=lambda: int(os.getenv("ALERT_DEBOUNCE_SAMPLES", "3"))
    )

    # Greek energy market defaults
    TIMEZONE: str = Field(
        default_factory=lambda: os.getenv("TIMEZONE", "Europe/Athens")
    )
    DEFAULT_CONTRACT_TYPE: str = Field(
        default_factory=lambda: os.getenv("DEFAULT_CONTRACT_TYPE", "Γ22")
    )
    DEFAULT_TARIFF_COLOR: str = Field(
        default_factory=lambda: os.getenv("DEFAULT_TARIFF_COLOR", "green")
    )

    # Simulator defaults
    SIMULATION_SPEED_DEFAULT: float = Field(
        default_factory=lambda: float(os.getenv("SIMULATION_SPEED_DEFAULT", "1.0"))
    )


# Global singleton instance
settings = Settings()
