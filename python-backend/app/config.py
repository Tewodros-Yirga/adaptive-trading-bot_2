from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    app_name: str = "Adaptive Trading Bot Python"
    port: int = 8000

    webhook_secret: str = "changeme"
    symbol: str = "XAUUSDm"
    adaptation_interval: int = 20
    simulation_mode: bool = True

    database_url: str = "postgresql://postgres:password@localhost:5432/trading_bot"
    mt_bridge_url: str = "http://localhost:5555"
    mt_bridge_secret: str = "bridge_secret_token"

    adaptation_min_closed_trades: int = 20
    adaptation_cooldown_trades: int = 20
    adaptation_lr: float = 0.002
    adaptation_max_change_pct: float = 0.3
    adaptation_confidence_threshold: float = 0.05
    adaptation_rollback_pf_drop: float = 0.15


settings = Settings()
