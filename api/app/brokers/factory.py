from app.brokers.alpaca import AlpacaBroker
from app.brokers.kis import KISBroker
from app.brokers.base import BrokerAdapter
from app.core.config import settings


def create_broker() -> BrokerAdapter:
    mode = settings.BROKER_MODE.lower()
    if mode == "alpaca":
        return AlpacaBroker()
    if mode == "kis":
        return KISBroker()
    raise ValueError(f"Unsupported BROKER_MODE: {settings.BROKER_MODE}")
