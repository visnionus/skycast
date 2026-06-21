from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Ключ OpenWeatherMap. Хранится только на сервере, в .env,
    # фронтенд его никогда не видит.
    owm_api_key: str = ""

    # Секрет для подписи JWT-токенов
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # токен живёт 7 дней

    class Config:
        env_file = ".env"


settings = Settings()
