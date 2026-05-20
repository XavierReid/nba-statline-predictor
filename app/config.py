"""Application configuration via environment variables (Pydantic Settings)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://nba:nba@localhost:5432/nba_predictor"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # Predictor weights — must sum to ~1.0; normalized at use site if not.
    predictor_w_recent: float = 0.50
    predictor_w_season: float = 0.30
    predictor_w_vs_opponent: float = 0.20

    # How many recent games to average for the "recent form" component
    predictor_recent_games_window: int = 10

    # Home/away multipliers
    predictor_home_adj: float = 1.05
    predictor_away_adj: float = 0.95

    # Rest day multipliers
    predictor_rest_back_to_back: float = 0.97
    predictor_rest_one_day: float = 1.00
    predictor_rest_two_plus: float = 1.02


settings = Settings()
