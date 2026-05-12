from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    bridge_port: int = Field(default=5555, alias="BRIDGE_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    mt_login: int = Field(default=0, alias="MT_LOGIN")
    mt_password: str = Field(default="", alias="MT_PASSWORD")
    mt_server: str = Field(default="", alias="MT_SERVER")
    mt_bridge_secret: str = Field(default="", alias="MT_BRIDGE_SECRET")
    # Keep Wine prefix off /tmp to avoid Render "temporary storage volume /tmp exceeded" evictions.
    mt_terminal_exe: str = Field(
        default="/home/wineuser/.wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe",
        alias="MT_TERMINAL_EXE",
    )

    # mt5linux (Wine + RPyC) defaults are localhost:18812
    # IMPORTANT: do not use `localhost` here. Some environments resolve it to IPv6
    # (`::1`) first, while mt5linux typically binds to IPv4 (`127.0.0.1`).
    mt5linux_host: str = Field(default="127.0.0.1", alias="MT5LINUX_HOST")
    mt5linux_port: int = Field(default=18812, alias="MT5LINUX_PORT")

    # If true, mock responses are returned when MetaTrader5 package/session is unavailable.
    mt_fallback_mode: bool = Field(default=True, alias="MT_FALLBACK_MODE")

    # Optional peer keepalive (ping backend service /health periodically).
    peer_healthcheck_url: str = Field(default="", alias="PEER_HEALTHCHECK_URL")
    peer_healthcheck_interval_seconds: int = Field(default=14 * 60, alias="PEER_HEALTHCHECK_INTERVAL_SECONDS")
    peer_healthcheck_timeout_seconds: int = Field(default=20, alias="PEER_HEALTHCHECK_TIMEOUT_SECONDS")
    peer_healthcheck_bearer_token: str = Field(default="", alias="PEER_HEALTHCHECK_BEARER_TOKEN")


settings = Settings()


def validate_required_settings() -> None:
    missing = []
    if not settings.mt_login:
        missing.append("MT_LOGIN")
    if not settings.mt_password:
        missing.append("MT_PASSWORD")
    if not settings.mt_server:
        missing.append("MT_SERVER")
    if not settings.mt_bridge_secret:
        missing.append("MT_BRIDGE_SECRET")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
