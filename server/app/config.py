from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sqlite_url: str = "sqlite+aiosqlite:///./carta.db"

    carta_host: str = "0.0.0.0"
    carta_port: int = 8443
    carta_use_tls: bool = False
    tls_cert: str = "certs/cert.pem"
    tls_key: str = "certs/key.pem"

    jwt_secret: str = "carta-dev-secret-change-me"
    shift_open_hour: int = 5  # 05:00 local
    timezone: str = "Europe/Moscow"

    # Первый / основной администратор (без дефолтных admin/user/accountant)
    admin_login: str = ""
    admin_password: str = ""
    admin_display_name: str = "Администратор"
    # Один раз: удалить логины admin/user/accountant из уже существующей БД
    carta_purge_default_users: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
