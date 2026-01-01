from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.strategies.base import StrategySignal
from app.brokers.base import BrokerAdapter, OrderRequest, AccountInfo
from app.services.risk import RiskManager, RiskException
from app.domain.models import Order, SignalLog, LogEntry
from datetime import datetime
import uuid

class ExecutionService:
    """
    Orchestrates the flow: Signal -> Risk Check -> Broker Order -> DB Record.
    """

    def __init__(self, db: AsyncSession, broker: BrokerAdapter, risk_manager: RiskManager):
        self.db = db
        self.broker = broker
        self.risk = risk_manager

    async def process_signals(self, signals: List[StrategySignal]):
        if not signals:
            return

        # 1. Fetch Account Info (for risk check)
        account = await self.broker.get_account_info()

        for signal in signals:
            try:
                # Log the Signal first
                await self._log_signal(signal)

                if signal.signal_type == 'HOLD':
                    continue

                # Convert Signal to Order Request
                # NOTE: Logic to determine Quantity implies 'sizing' logic. 
                # For now, we assume fixed quantity or based on signal strength.
                qty = self._calculate_quantity(signal, account) 
                
                side = 'buy' if signal.signal_type == 'BUY' else 'sell'
                order_req = OrderRequest(
                    symbol=signal.symbol,
                    qty=qty,
                    side=side,
                    type='market' # Default to market for now
                )

                # Fetch current price estimate (for risk check)
                # In real impl, broker.get_last_price(symbol)
                price_estimate = 100.0 # MOCK

                # 2. Risk Validation
                await self.risk.validate_order(account, order_req, price_estimate)

                # 3. Submit to Broker
                result = await self.broker.submit_order(order_req)

                # 4. Record Order in DB
                db_order = Order(
                    client_order_id=result.client_order_id,
                    broker_order_id=result.broker_order_id,
                    symbol=result.symbol,
                    side=order_req.side,
                    type=order_req.type,
                    qty=result.qty,
                    status=result.status
                )
                self.db.add(db_order)
                
                # Log success
                self.db.add(LogEntry(level="INFO", source="Execution", message=f"Order Placed: {result.client_order_id}"))

            except RiskException as e:
                # Handled in RiskManager log
                pass
            except Exception as e:
                self.db.add(LogEntry(level="ERROR", source="Execution", message=f"Failed to process signal: {str(e)}"))
        
        await self.db.commit()

    def _calculate_quantity(self, signal: StrategySignal, account: AccountInfo) -> float:
        # Simple Logic: Buy 1 unit. 
        # TODO: Implement proper position sizing based on portfolio value & signal strength
        return 1.0

    async def _log_signal(self, signal: StrategySignal):
        log = SignalLog(
            strategy_name="Unknown", # Should pass strategy name in context
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            signal_strength=signal.strength,
            raw_data={"rationale": signal.rationale}
        )
        self.db.add(log)
