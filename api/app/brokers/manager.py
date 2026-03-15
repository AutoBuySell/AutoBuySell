from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from app.brokers.base import BrokerAdapter
from app.domain.models import BrokerAccount

logger = logging.getLogger(__name__)


class BrokerManager:
    """Registry of active broker instances, keyed by account UUID."""

    def __init__(self):
        self._brokers: dict[UUID, BrokerAdapter] = {}
        self._accounts: dict[UUID, BrokerAccount] = {}

    async def initialize(self, accounts: list[BrokerAccount]):
        """Create and store broker instances for all active accounts."""
        from app.brokers.factory import create_broker_for_account

        for acct in accounts:
            try:
                broker = create_broker_for_account(acct)
                self._brokers[acct.id] = broker
                self._accounts[acct.id] = acct
                logger.info(
                    f"Initialized broker for account '{acct.name}' ({acct.broker_type})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to initialize broker for account '{acct.name}': {e}"
                )

    def get(self, account_id: UUID) -> BrokerAdapter:
        """Get broker for a specific account. Raises KeyError if not found."""
        return self._brokers[account_id]

    def get_account(self, account_id: UUID) -> BrokerAccount:
        """Get account metadata."""
        return self._accounts[account_id]

    def all_active(self) -> list[tuple[UUID, BrokerAdapter]]:
        """Return all active (account_id, broker) pairs."""
        return list(self._brokers.items())

    def all_accounts(self) -> list[BrokerAccount]:
        """Return all registered account objects."""
        return list(self._accounts.values())

    def account_ids(self) -> list[UUID]:
        """Return all registered account IDs."""
        return list(self._brokers.keys())

    def default_account_id(self) -> Optional[UUID]:
        """Return the first account ID, or None if empty."""
        if self._brokers:
            return next(iter(self._brokers))
        return None
