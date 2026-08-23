import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file automatically
load_dotenv()


class Settings(BaseModel):
    app_env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    
    # Firebase configuration
    firebase_enabled: bool = Field(default_factory=lambda: os.getenv("FIREBASE_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
    firebase_service_account_key: str = Field(default_factory=lambda: os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "serviceAccountKey.json"))
    firebase_credentials_json: str = Field(default_factory=lambda: os.getenv("FIREBASE_CREDENTIALS_JSON", ""))
    firebase_storage_bucket: str = Field(default_factory=lambda: os.getenv("FIREBASE_STORAGE_BUCKET", ""))

    # Blob Storage Provider ('supabase' | 'r2' | 'local')
    storage_provider: str = Field(default_factory=lambda: os.getenv("STORAGE_PROVIDER", "supabase").lower())

    # Supabase Storage Configuration
    supabase_url: str = Field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))))
    supabase_storage_bucket: str = Field(default_factory=lambda: os.getenv("SUPABASE_STORAGE_BUCKET", "novels"))
    supabase_storage_public_url: str = Field(default_factory=lambda: os.getenv("SUPABASE_STORAGE_PUBLIC_URL", ""))

    # Cloudflare R2 Storage Configuration (S3-Compatible Free 10GB Storage)
    cloudflare_account_id: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))
    cloudflare_r2_access_key_id: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", ""))
    cloudflare_r2_secret_access_key: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", ""))
    cloudflare_r2_bucket_name: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_BUCKET_NAME", ""))
    cloudflare_r2_public_url: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_PUBLIC_URL", ""))

    # Model defaults
    default_provider: str = Field(default="gemini")
    default_translation_model: str = Field(default="gemini-2.5-flash")
    default_extraction_model: str = Field(default="gemini-2.5-flash")
    default_qa_model: str = Field(default="gemini-2.5-flash")
    default_gemini_model: str = Field(default="gemini-2.5-flash")
    default_anthropic_model: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    )

    max_upload_bytes: int = Field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    )
    max_text_input_chars: int = Field(
        default_factory=lambda: int(os.getenv("MAX_TEXT_INPUT_CHARS", str(2 * 1024 * 1024)))
    )
    
    # Chunking settings
    txt_chunk_min_words: int = 1500
    txt_chunk_max_words: int = 3000
    previous_context_words: int = 150
    
    # Cache settings
    enable_prompt_caching: bool = True
    cache_ttl_seconds: int = 300  # Default 5 min TTL

    # Database & Structured Storage configuration
    database_url: str = Field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./storage/local_db.sqlite3"))
    structured_storage_backend: str = Field(default_factory=lambda: os.getenv("STRUCTURED_STORAGE_BACKEND", "legacy").lower())
    structured_storage_read_source: str = Field(default_factory=lambda: os.getenv("STRUCTURED_STORAGE_READ_SOURCE", "legacy").lower())
    db_pool_size: int = Field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "5")))
    db_max_overflow: int = Field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "5")))
    db_pool_timeout_seconds: int = Field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "10")))
    book_bible_write_token: str = Field(default_factory=lambda: os.getenv("BOOK_BIBLE_WRITE_TOKEN", ""))


settings = Settings()

