from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Inspire API"
    ENVIRONMENT: str = "development"

    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
        "https://lms-ui-amber.vercel.app",
    ]

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    # Hosted session poolers commonly have small hard limits. Keep each API or
    # worker process bounded so one process cannot consume every DB session.
    DATABASE_POOL_SIZE: int = 3
    DATABASE_MAX_OVERFLOW: int = 0
    DATABASE_POOL_TIMEOUT_SECONDS: int = 20
    DATABASE_POOL_RECYCLE_SECONDS: int = 300

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    JWT_PRIVATE_KEY_PATH: str = "keys/jwt_private.pem"
    JWT_PUBLIC_KEY_PATH: str = "keys/jwt_public.pem"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 90
    REFRESH_TOKEN_REUSE_GRACE_SECONDS: int = 60
    SSO_TICKET_EXPIRE_SECONDS: int = 60

    AUTHENTICATOR_ENCRYPTION_KEY: str = ""
    AUTHENTICATOR_ISSUER: str = "Inspire College"
    AUTHENTICATOR_SETUP_EXPIRE_MINUTES: int = 10080  # Seven days; setup links remain single-use.
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    AUTHENTICATOR_MAX_ATTEMPTS: int = 5
    AUTHENTICATOR_LOCK_MINUTES: int = 5
    AUTHENTICATOR_IP_RATE_LIMIT_PER_HOUR: int = 30

    # Optional CMS-only shared sign-in. Keep the real values in the deployed
    # environment; ordinary users continue to use their Authenticator code.
    CMS_COMMON_LOGIN_EMAIL: str = ""
    CMS_COMMON_LOGIN_CODE: str = ""

    MAILJET_API_KEY: str = ""
    MAILJET_SECRET_KEY: str = ""
    MAILJET_FROM_EMAIL: str = ""
    MAILJET_FROM_NAME: str = "Inspire College"
    PUBLIC_FORM_FROM_EMAIL: str = "enrol@inspire.college"
    PUBLIC_FORM_FROM_NAME: str = "Inspire College"
    PUBLIC_FORM_RECIPIENT_EMAIL: str = "enrol@inspire.college"
    AUTHENTICATOR_INVITATION_SUBJECT: str = "Set up your Inspire College Authenticator"

    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/v1/lms/integrations/google/callback"
    GOOGLE_TOKEN_ENCRYPTION_KEY: str = ""
    GOOGLE_OAUTH_STATE_EXPIRE_MINUTES: int = 10
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    ZOOM_REDIRECT_URI: str = "http://localhost:8000/api/v1/lms/integrations/zoom/callback"
    ZOOM_WEBHOOK_SECRET: str = ""
    ZOOM_TOKEN_ENCRYPTION_KEY: str = ""
    ZOOM_MEETING_SDK_KEY: str = ""
    ZOOM_MEETING_SDK_SECRET: str = ""
    ZOOM_OAUTH_STATE_EXPIRE_MINUTES: int = 10
    LMS_UI_URL: str = "https://lms-ui-amber.vercel.app"
    CMS_UI_URL: str = "http://localhost:5173"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.6-luna"
    OPENAI_TIMEOUT_SECONDS: int = 45
    OPENAI_MAX_OUTPUT_TOKENS: int = 700
    COURSE_ASSISTANT_MAX_PDF_MB: int = 25

    MEDIA_BUCKET: str = ""
    MEDIA_REGION: str = "ap-northeast-1"
    MEDIA_ENDPOINT_URL: str = ""
    MEDIA_PUBLIC_BASE_URL: str = ""
    MEDIA_ACCESS_KEY_ID: str = ""
    MEDIA_SECRET_ACCESS_KEY: str = ""
    MEDIA_UPLOAD_EXPIRE_SECONDS: int = 900

    # Vimeo is intentionally server-only.  Never expose this token to the LMS browser.
    VIMEO_ACCESS_TOKEN: str = ""
    VIMEO_ROOT_FOLDER: str = "INSPIRE COLLEGE"
    VIMEO_EMBED_DOMAINS: list[str] = []
    VIMEO_MAX_UPLOAD_MB: int = 20480


settings = Settings()
