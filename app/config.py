"""Environment-based configuration settings."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str = "sqlite:///./certificates.db"
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Storage
    output_dir: str = "./output"
    
    # Limits
    max_recipients: int = 1000
    chunk_size: int = 10
    max_event_name_length: int = 200
    max_recipient_name_length: int = 200
    max_recipient_email_length: int = 320
    
    # Recovery
    stale_minutes: int = 30
    
    # Optional rate limiting
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 10
    rate_limit_period: int = 60
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


settings = Settings()
