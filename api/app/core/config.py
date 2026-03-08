from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Literal

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

    # Broker selection
    BROKER_MODE: Literal["alpaca", "kis"] = "alpaca"

    # Alpaca
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_SECRET_KEY: Optional[str] = None
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"

    # Korea Investment & Securities (KIS)
    KIS_APP_KEY: Optional[str] = None
    KIS_APP_SECRET: Optional[str] = None
    KIS_ACCOUNT_CANO: Optional[str] = None  # 8-digit account prefix
    KIS_ACCOUNT_ACNT_PRDT_CD: Optional[str] = None  # 2-digit account product code
    KIS_BASE_URL: str = "https://openapi.koreainvestment.com:9443"
    KIS_IS_PAPER: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()
