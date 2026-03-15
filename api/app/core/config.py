from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Literal, List
import json


class Settings(BaseSettings):
    PROJECT_NAME: str = "AutoBuySell"
    VERSION: str = "2.0.0"

    # Database
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Multi-account configuration (JSON array)
    # If set, overrides single-account BROKER_MODE + individual credential env vars.
    BROKER_ACCOUNTS: Optional[str] = None  # JSON string

    # Legacy single-account (fallback when BROKER_ACCOUNTS is not set)
    BROKER_MODE: Literal["alpaca", "kis"] = "alpaca"

    # Alpaca (legacy single-account)
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_SECRET_KEY: Optional[str] = None
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # Korea Investment & Securities (legacy single-account)
    KIS_APP_KEY: Optional[str] = None
    KIS_APP_SECRET: Optional[str] = None
    KIS_ACCOUNT_CANO: Optional[str] = None
    KIS_ACCOUNT_ACNT_PRDT_CD: Optional[str] = None
    KIS_BASE_URL: str = "https://openapi.koreainvestment.com:9443"
    KIS_IS_PAPER: bool = True
    KIS_US_EXCHANGE: str = "NASD"
    KIS_US_PRICE_EXCD: str = "NAS"
    KIS_US_CURRENCY: str = "USD"

    def get_broker_accounts_config(self) -> List[dict]:
        """Parse multi-account config. Falls back to single-account env vars."""
        if self.BROKER_ACCOUNTS:
            return json.loads(self.BROKER_ACCOUNTS)

        # Fallback: build single account from legacy env vars
        if self.BROKER_MODE == "alpaca":
            return [
                {
                    "name": "alpaca-default",
                    "broker_type": "alpaca",
                    "credentials": {
                        "api_key": self.ALPACA_API_KEY,
                        "secret_key": self.ALPACA_SECRET_KEY,
                    },
                    "config": {
                        "base_url": self.ALPACA_BASE_URL,
                    },
                }
            ]
        elif self.BROKER_MODE == "kis":
            return [
                {
                    "name": "kis-default",
                    "broker_type": "kis",
                    "credentials": {
                        "app_key": self.KIS_APP_KEY,
                        "app_secret": self.KIS_APP_SECRET,
                        "cano": self.KIS_ACCOUNT_CANO,
                        "acnt_prdt_cd": self.KIS_ACCOUNT_ACNT_PRDT_CD,
                    },
                    "config": {
                        "base_url": self.KIS_BASE_URL,
                        "is_paper": self.KIS_IS_PAPER,
                        "us_exchange": self.KIS_US_EXCHANGE,
                        "us_price_excd": self.KIS_US_PRICE_EXCD,
                        "us_currency": self.KIS_US_CURRENCY,
                    },
                }
            ]
        return []

    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )


settings = Settings()
