"""Runtime configuration (environment-driven, prefix ``ERP_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, model_validator
# pyrefly: ignore[missing-import]
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_METASTORE_PATH = PROJECT_ROOT / "app" / "metastore" / "metastore_schema.json"
DEFAULT_SQL_DUMP_PATH = WORKSPACE_ROOT / "ledger (34).sql"

# Tables that must never be exposed to the AI read path (credential stores).
RESTRICTED_TABLES: frozenset[str] = frozenset({"gsp_api", "gsp_credentials"})

# Column-name fragments treated as secrets and stripped from every table.
SENSITIVE_COLUMN_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "otp",
    "api_key",
    "apikey",
)

# Raw binary payloads are never useful for reporting and are excluded.
EXCLUDED_COLUMN_TYPES: tuple[str, ...] = ("blob", "longblob", "mediumblob", "tinyblob", "binary", "varbinary")

LLMProvider = Literal["openai", "vllm", "ollama", "gemini", "openrouter", "huggingface"]

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "model": "Qwen/Qwen2.5-Coder-14B-Instruct",
        "timeout_s": 60.0,
        "api_key": None,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
        "timeout_s": 60.0,
        "api_key": "ollama",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "timeout_s": 30.0,
        "api_key": None,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.8-flash",
        "timeout_s": 30.0,
        "api_key": None,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen-2.5-coder-32b-instruct",
        "timeout_s": 45.0,
        "api_key": None,
    },
    "huggingface": {
        "base_url": "https://router.huggingface.co/v1",
        "model": "Qwen/Qwen2.5-VL-72B-Instruct:cheapest",
        "timeout_s": 90.0,
        "api_key": None,
    },
}


def build_mysql_url(
    db_url: str | None = None,
    host: str | None = None,
    port: int = 3306,
    user: str | None = None,
    password: str | None = None,
    name: str = "ledger",
) -> str | None:
    """Build a MySQL SQLAlchemy connection URL safely with URL escaping."""
    if db_url:
        return db_url
    if host and user:
        pwd_part = f":{quote_plus(password)}" if password else ""
        return f"mysql+pymysql://{user}{pwd_part}@{host}:{port}/{name}"
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", env_file=".env", extra="ignore")

    tenant_id: str = "shree_shashwatraj_agro"
    sql_dialect: str = "mysql"
    database_name: str = "ledger"

    # Read-only DSN, e.g. mysql+pymysql://ai_reader:***@127.0.0.1:3306/ledger
    db_url: str | None = None
    # Database connection parameters (Milestone 2)
    db_host: str | None = Field(default=None, validation_alias=AliasChoices("ERP_DB_HOST", "DB_HOST"))
    db_port: int = Field(default=3306, validation_alias=AliasChoices("ERP_DB_PORT", "DB_PORT"))
    db_user: str | None = Field(default=None, validation_alias=AliasChoices("ERP_DB_USER", "DB_USER"))
    db_password: str | None = Field(default=None, validation_alias=AliasChoices("ERP_DB_PASSWORD", "DB_PASSWORD"))
    db_name: str | None = Field(default=None, validation_alias=AliasChoices("ERP_DB_NAME", "DB_NAME"))
    db_statement_timeout_ms: int = Field(default=5000, validation_alias=AliasChoices("ERP_DB_STATEMENT_TIMEOUT_MS", "DB_STATEMENT_TIMEOUT_MS"))
    db_pool_size: int = Field(default=5, validation_alias=AliasChoices("ERP_DB_POOL_SIZE", "DB_POOL_SIZE"))
    db_max_overflow: int = Field(default=10, validation_alias=AliasChoices("ERP_DB_MAX_OVERFLOW", "DB_MAX_OVERFLOW"))
    db_pool_timeout: float = Field(default=10.0, validation_alias=AliasChoices("ERP_DB_POOL_TIMEOUT", "DB_POOL_TIMEOUT"))
    db_pool_recycle: int = Field(default=1800, validation_alias=AliasChoices("ERP_DB_POOL_RECYCLE", "DB_POOL_RECYCLE"))

    sql_dump_path: Path = DEFAULT_SQL_DUMP_PATH
    metastore_path: Path = DEFAULT_METASTORE_PATH

    max_row_limit: int = Field(default=1000, ge=1, le=10000)

    def build_mysql_url(self) -> str | None:
        """Helper to build MySQL connection URL from configured settings."""
        return build_mysql_url(
            db_url=self.db_url,
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_password,
            name=self.db_name or self.database_name,
        )

    # Inference (OpenAI-compatible / vLLM / Ollama / Gemini / OpenRouter)
    llm_provider: LLMProvider = "vllm"
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "Qwen/Qwen2.5-Coder-14B-Instruct"
    llm_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ERP_LLM_API_KEY", "LLM_API_KEY"))
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 1

    # Server, Security & Auth (Milestone 3)
    dev_mode: bool = Field(default=True, validation_alias=AliasChoices("ERP_DEV_MODE", "DEV_MODE"))
    jwt_secret: str = Field(default="erp-secret-key-change-in-prod", validation_alias=AliasChoices("ERP_JWT_SECRET", "JWT_SECRET"))
    jwt_algorithm: str = Field(default="HS256", validation_alias=AliasChoices("ERP_JWT_ALGORITHM", "JWT_ALGORITHM"))
    cors_origins: list[str] = Field(default_factory=lambda: ["*"], validation_alias=AliasChoices("ERP_CORS_ORIGINS", "CORS_ORIGINS"))

    # Provider-Specific API Keys (supports both ERP_ prefix and standard provider env vars)
    gemini_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ERP_GEMINI_API_KEY", "GEMINI_API_KEY"))
    openrouter_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ERP_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"))
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ERP_OPENAI_API_KEY", "OPENAI_API_KEY"))
    hf_token: str | None = Field(default=None, validation_alias=AliasChoices("HF_TOKEN", "ERP_HF_TOKEN"))
    hf_base_url: str | None = Field(default=None, validation_alias=AliasChoices("HF_BASE_URL", "ERP_HF_BASE_URL"))
    hf_vision_model: str | None = Field(default=None, validation_alias=AliasChoices("HF_VISION_MODEL", "ERP_HF_VISION_MODEL"))

    # Sub-Schema Router (Milestone 5)
    enable_schema_router: bool = Field(default=False, validation_alias=AliasChoices("ERP_ENABLE_SCHEMA_ROUTER", "ENABLE_SCHEMA_ROUTER"))
    schema_router_threshold: int = Field(default=50, validation_alias=AliasChoices("ERP_SCHEMA_ROUTER_THRESHOLD", "SCHEMA_ROUTER_THRESHOLD"))
    schema_router_top_k: int = Field(default=6, validation_alias=AliasChoices("ERP_SCHEMA_ROUTER_TOP_K", "SCHEMA_ROUTER_TOP_K"))
    schema_router_max_k: int = Field(default=12, validation_alias=AliasChoices("ERP_SCHEMA_ROUTER_MAX_K", "SCHEMA_ROUTER_MAX_K"))
    schema_router_embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", validation_alias=AliasChoices("ERP_SCHEMA_ROUTER_EMBEDDING_MODEL", "SCHEMA_ROUTER_EMBEDDING_MODEL"))

    @model_validator(mode="after")
    def _populate_provider_defaults(self) -> "Settings":
        provider = self.llm_provider
        defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["vllm"])

        if provider == "huggingface":
            if self.hf_base_url:
                self.llm_base_url = self.hf_base_url
            if self.hf_vision_model:
                self.llm_model = self.hf_vision_model

        # If base_url was not explicitly supplied, set provider default
        if "llm_base_url" not in self.model_fields_set or not self.llm_base_url:
            self.llm_base_url = defaults["base_url"]

        # If model was not explicitly supplied, set provider default
        if "llm_model" not in self.model_fields_set or not self.llm_model:
            self.llm_model = defaults["model"]

        # If timeout was not explicitly supplied, set provider default
        if "llm_timeout_s" not in self.model_fields_set or self.llm_timeout_s is None:
            self.llm_timeout_s = defaults["timeout_s"]

        # If llm_api_key was not explicitly supplied, check provider-specific keys or defaults
        if "llm_api_key" not in self.model_fields_set or not self.llm_api_key:
            if provider == "gemini" and self.gemini_api_key:
                self.llm_api_key = self.gemini_api_key
            elif provider == "openrouter" and self.openrouter_api_key:
                self.llm_api_key = self.openrouter_api_key
            elif provider == "huggingface" and self.hf_token:
                self.llm_api_key = self.hf_token
            elif provider == "openai" and self.openai_api_key:
                self.llm_api_key = self.openai_api_key
            else:
                self.llm_api_key = defaults.get("api_key")

        return self

    def get_provider_config(self, provider: LLMProvider | None = None) -> dict[str, Any]:
        """Resolve base_url, model, api_key, and timeout_s for any provider."""
        p = provider or self.llm_provider
        defaults = PROVIDER_DEFAULTS.get(p, PROVIDER_DEFAULTS["vllm"])

        base_url = self.llm_base_url if (p == self.llm_provider and self.llm_base_url) else defaults["base_url"]
        model = self.llm_model if (p == self.llm_provider and self.llm_model) else defaults["model"]
        timeout = self.llm_timeout_s if (p == self.llm_provider and self.llm_timeout_s is not None) else defaults["timeout_s"]

        api_key = None
        if p == "gemini":
            api_key = self.gemini_api_key or (self.llm_api_key if p == self.llm_provider else None) or defaults.get("api_key")
        elif p == "openrouter":
            api_key = self.openrouter_api_key or (self.llm_api_key if p == self.llm_provider else None) or defaults.get("api_key")
        elif p == "openai":
            api_key = self.openai_api_key or (self.llm_api_key if p == self.llm_provider else None) or defaults.get("api_key")
        else:
            api_key = self.llm_api_key if p == self.llm_provider else defaults.get("api_key")

        return {
            "provider": p,
            "base_url": base_url,
            "model": model,
            "timeout_s": timeout,
            "api_key": api_key,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
