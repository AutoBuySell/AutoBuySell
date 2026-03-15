from app.brokers.alpaca import AlpacaBroker
from app.brokers.kis import KISBroker
from app.brokers.base import BrokerAdapter
from app.domain.models import BrokerAccount


def create_broker_for_account(account: BrokerAccount) -> BrokerAdapter:
    """Create a broker instance from a BrokerAccount DB row."""
    creds = account.credentials or {}
    config = account.config or {}
    broker_type = account.broker_type.lower()

    if broker_type == "alpaca":
        return AlpacaBroker(
            api_key=creds.get("api_key"),
            secret_key=creds.get("secret_key"),
            base_url=config.get("base_url"),
        )
    elif broker_type == "kis":
        return KISBroker(
            app_key=creds.get("app_key"),
            app_secret=creds.get("app_secret"),
            cano=creds.get("cano"),
            acnt_prdt_cd=creds.get("acnt_prdt_cd"),
            base_url=config.get("base_url"),
            is_paper=config.get("is_paper", True),
            us_exchange=config.get("us_exchange", "NASD"),
            us_price_excd=config.get("us_price_excd", "NAS"),
            us_currency=config.get("us_currency", "USD"),
        )
    else:
        raise ValueError(f"Unsupported broker_type: {account.broker_type}")


def create_broker() -> BrokerAdapter:
    """Legacy single-broker creation from env vars. Backward compatible."""
    from app.core.config import settings

    mode = settings.BROKER_MODE.lower()
    if mode == "alpaca":
        return AlpacaBroker()
    if mode == "kis":
        return KISBroker()
    raise ValueError(f"Unsupported BROKER_MODE: {settings.BROKER_MODE}")
