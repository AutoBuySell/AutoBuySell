"""
Central Strategy Registry

Maintains a single source of truth for all trading strategies.
"""

from app.strategies.mean_reversion import MeanReversionStrategy

# Single source of truth for all strategies
STRATEGY_REGISTRY = {
    "MeanReversion_v1": MeanReversionStrategy()
}


def get_strategy(strategy_name: str):
    """Get a strategy instance by name"""
    if strategy_name not in STRATEGY_REGISTRY:
        raise ValueError(f"Strategy '{strategy_name}' not found. Available: {list(STRATEGY_REGISTRY.keys())}")
    return STRATEGY_REGISTRY[strategy_name]


def get_all_strategies():
    """Get all registered strategies"""
    return STRATEGY_REGISTRY


def get_strategy_names():
    """Get list of all registered strategy names"""
    return list(STRATEGY_REGISTRY.keys())
