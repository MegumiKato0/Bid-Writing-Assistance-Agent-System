from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:latest"
    agent_temperature: float = 0.2
    max_review_rounds: int = 1
    extract_chunk_chars: int = 28000
    extract_chunk_overlap: int = 800
    max_pages_for_review: int = 24
    llm_http_trust_env: bool = True

    # Phase 1 auto export
    phase1_auto_export: bool = True
    phase1_auto_export_dir: str = ""

    # Phase 2 auto export
    phase2_auto_export: bool = True
    phase2_auto_export_dir: str = ""


settings = Settings()
