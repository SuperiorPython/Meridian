from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_", env_file=".env")

    env: str = "dev"
    sec_user_agent: str = "Meridian Research unset@example.com"
    cache_dir: Path = Path("data/cache")
    db_path: Path = Path("data/meridian.db")
    # Secret access key for the API. Unset is allowed only when env == "dev".
    api_token: str = ""
    # Comma-separated browser origins allowed to call the API (your frontend's address).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
