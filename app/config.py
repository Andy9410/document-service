from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "ai_chat_db"
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_ssl: str = "disable"

    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_base_url: str = "https://api.cloudflare.com/client/v4"
    cloudflare_embedding_model: str = "@cf/baai/bge-base-en-v1.5"

    jwt_secret: str = ""

    max_file_size_mb: int = 20
    max_files_per_upload: int = 5

    chunk_size: int = 512
    chunk_overlap: int = 64
    min_chunk_length: int = 80

    search_top_k: int = 8
    similarity_threshold: float = 0.72

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?sslmode={self.db_ssl}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
