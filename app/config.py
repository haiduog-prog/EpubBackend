import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file automatically
load_dotenv()


class Settings(BaseModel):
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    
    # Firebase configuration
    firebase_enabled: bool = Field(default_factory=lambda: os.getenv("FIREBASE_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
    firebase_service_account_key: str = Field(default_factory=lambda: os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "serviceAccountKey.json"))
    firebase_credentials_json: str = Field(default_factory=lambda: os.getenv("FIREBASE_CREDENTIALS_JSON", ""))
    firebase_storage_bucket: str = Field(default_factory=lambda: os.getenv("FIREBASE_STORAGE_BUCKET", ""))

    # Cloudflare R2 Storage Configuration (S3-Compatible Free 10GB Storage)
    cloudflare_account_id: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))
    cloudflare_r2_access_key_id: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", ""))
    cloudflare_r2_secret_access_key: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", ""))
    cloudflare_r2_bucket_name: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_BUCKET_NAME", ""))
    cloudflare_r2_public_url: str = Field(default_factory=lambda: os.getenv("CLOUDFLARE_R2_PUBLIC_URL", ""))

    # Model defaults (Google Gemini Only)
    default_provider: str = Field(default="gemini")
    default_translation_model: str = Field(default="gemini-2.5-flash")
    default_extraction_model: str = Field(default="gemini-2.5-flash")
    default_qa_model: str = Field(default="gemini-2.5-flash")
    default_gemini_model: str = Field(default="gemini-2.5-flash")
    
    # Chunking settings
    txt_chunk_min_words: int = 1500
    txt_chunk_max_words: int = 3000
    previous_context_words: int = 150
    
    # Cache settings
    enable_prompt_caching: bool = True
    cache_ttl_seconds: int = 300  # Default 5 min TTL


settings = Settings()

