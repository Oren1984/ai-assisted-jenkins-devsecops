"""Service configuration, read from environment variables."""

import os
from dataclasses import dataclass


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"  # "mock" or "llm"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    fallback_to_mock: bool = False
    ingest_token: str = ""
    database_path: str = "review.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            provider=os.environ.get("AI_PROVIDER", "mock").strip().lower(),
            llm_base_url=os.environ.get("LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", ""),
            llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
            llm_max_retries=int(os.environ.get("LLM_MAX_RETRIES", "2")),
            fallback_to_mock=_flag("AI_FALLBACK_TO_MOCK", "false"),
            ingest_token=os.environ.get("AI_INGEST_TOKEN", ""),
            database_path=os.environ.get("AI_DATABASE_PATH", "review.db"),
        )
