from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Inspire API"
    ENVIRONMENT: str = "development"

    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    JWT_PRIVATE_KEY_PATH: str = "keys/jwt_private.pem"
    JWT_PUBLIC_KEY_PATH: str = "keys/jwt_public.pem"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    SSO_TICKET_EXPIRE_SECONDS: int = 60

    AUTHENTICATOR_ENCRYPTION_KEY: str = ""
    AUTHENTICATOR_ISSUER: str = "Inspire College"
    AUTHENTICATOR_SETUP_EXPIRE_MINUTES: int = 30
    AUTHENTICATOR_MAX_ATTEMPTS: int = 5
    AUTHENTICATOR_LOCK_MINUTES: int = 5
    AUTHENTICATOR_IP_RATE_LIMIT_PER_HOUR: int = 30

    MAILJET_API_KEY: str = ""
    MAILJET_SECRET_KEY: str = ""
    MAILJET_FROM_EMAIL: str = ""
    MAILJET_FROM_NAME: str = "Inspire College"
    AUTHENTICATOR_INVITATION_SUBJECT: str = "Set up your Inspire College Authenticator"

    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/lms/integrations/google/callback"
    GOOGLE_TOKEN_ENCRYPTION_KEY: str = ""
    GOOGLE_OAUTH_STATE_EXPIRE_MINUTES: int = 10
    LMS_UI_URL: str = "http://localhost:5174"
    CMS_UI_URL: str = "http://localhost:5173"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.6-luna"
    COURSE_ASSISTANT_MAX_PDF_MB: int = 25


settings = Settings()
