from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Lex-Advisor RAG Service API"
    app_version: str = "1.0.0"
    api_key: str = "dev-api-key"
    default_tenant: str = "ph-balta-doamnei"
    max_file_size_mib: int = 50
    allowed_mime_types: str = "text/html,application/pdf,text/plain,text/markdown"
    webhook_secret: str = "dev-webhook-secret"
    database_url: str = "sqlite:///./rag.db"  # override with postgresql+psycopg:// for production
    redis_url: str = "redis://localhost:6379/0"

    # Gemini
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768
    llm_model: str = "gemini-2.5-flash"

    # OpenTelemetry (env-driven; unset = no-op)
    otel_service_name: str = "citydock-rag-service"
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_protocol: str = "grpc"
    otel_resource_attributes: str = "deployment.environment=local"

    @property
    def allowed_mime_set(self) -> set[str]:
        return {mime.strip() for mime in self.allowed_mime_types.split(",") if mime.strip()}


settings = Settings()
