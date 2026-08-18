from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MissionFlow"
    app_env: str = "development"
    database_url: str = "sqlite:///./missionflow.db"
    secret_key: str = "development-only"
    cors_origins: str = "http://localhost:8000"
    app_username: str = "admin"
    app_password: str = "development-only"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_allowed_email: str = ""
    local_login_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
