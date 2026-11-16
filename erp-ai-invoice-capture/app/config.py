from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAPTURE_", env_file=".env", extra="ignore")

    tenant_id: str = "shree_shashwatraj_agro"
    shree_base_url: str = "http://localhost:5000"
    orchestrator_url: str = "http://localhost:8000"
    scan_dir: Path = Path("./capture-scans")
    max_file_size_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    max_batch_size: int = Field(default=10, ge=1)
    confirm_roles: str = "admin"
    request_timeout_s: float = Field(default=150.0, gt=0)

    @property
    def confirmation_roles(self) -> frozenset[str]:
        return frozenset(role.strip().lower() for role in self.confirm_roles.split(",") if role.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

