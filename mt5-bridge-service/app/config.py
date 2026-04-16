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
    mt_terminal_exe: str = Field(default="/root/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe", alias="MT_TERMINAL_EXE")

    # If true, mock responses are returned when MetaTrader5 package/session is unavailable.
    mt_fallback_mode: bool = Field(default=True, alias="MT_FALLBACK_MODE")


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
