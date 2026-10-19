from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_", env_file=".env")

    env: str = "dev"
    sec_user_agent: str = "Meridian Research unset@example.com"
    cache_dir: Path = Path("data/cache")
    db_path: Path = Path("data/meridian.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
