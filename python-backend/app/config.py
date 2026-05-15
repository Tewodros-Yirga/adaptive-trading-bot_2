from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    app_name: str = "Adaptive Trading Bot Python"
    port: int = 8000

    webhook_secret: str = "changeme"
    symbol: str = "XAUUSDm"
    adaptation_interval: int = 20
    simulation_mode: bool = True

    # Auth — set these in HuggingFace secrets
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="", validation_alias="ADMIN_PASSWORD")
    jwt_secret_key: str = Field(default="", validation_alias="JWT_SECRET_KEY")

    # MongoDB Atlas — replaces PostgreSQL database_url
    mongodb_uri: str = Field(
        default="mongodb://localhost:27017/trading_bot",
        validation_alias="MONGODB_URI",
    )

    mt_bridge_url: str = "http://localhost:5555"
    mt_bridge_secret: str = "bridge_secret_token"
    mt_bridge_hf_token: str = ""

    adaptation_min_closed_trades: int = 20
    adaptation_cooldown_trades: int = 20
    adaptation_lr: float = 0.002
    adaptation_max_change_pct: float = 0.3
    adaptation_confidence_threshold: float = 0.05
    adaptation_rollback_pf_drop: float = 0.15

    # URL of the standalone backtester microservice.
    # Used by GET /strategies/{name}/search-status to proxy live status.
    # Falls back to MongoDB data if the service is unreachable.
    backtester_service_url: str = Field(
        default="http://localhost:8001",
        validation_alias="BACKTESTER_SERVICE_URL",
    )
    backtester_service_timeout: float = 2.0   # seconds; fail fast, fall back to DB

    # Optional peer keepalive
    peer_healthcheck_url: str = ""
    peer_healthcheck_interval_seconds: int = 14 * 60
    peer_healthcheck_timeout_seconds: int = 20
    peer_healthcheck_bearer_token: str = ""


settings = Settings()