from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.strategies.base import StrategySignal
from app.brokers.base import BrokerAdapter, OrderRequest, AccountInfo
from app.services.risk import RiskManager, RiskException
from app.domain.models import Order, SignalLog, LogEntry
from datetime import datetime
import uuid
from app.api.ws import manager

class ExecutionService:
    """
    Orchestrates the flow: Signal -> Risk Check -> Broker Order -> DB Record.
    """

    def __init__(self, broker: BrokerAdapter, risk_manager: RiskManager):
        self.broker = broker
        self.risk = risk_manager

    async def process_signals(self, db: AsyncSession, signals: List[StrategySignal]):
        if not signals:
            return

        # 1. Fetch Account Info (for risk check)
        account = await self.broker.get_account_info()

        for signal in signals:
            try:
                # Log the Signal first
                await self._log_signal(db, signal)

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
                # Use the current_price from signal metadata (already fetched by strategy)
                price_estimate = signal.metadata.get('current_price', 0.0)
                
                if price_estimate == 0.0:
                    logger.warning(f"No current_price in signal metadata for {signal.symbol}, skipping")
                    continue

                # 2. Risk Validation
                await self.risk.validate_order(db, account, order_req, price_estimate)

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
                db.add(db_order)
                
                # Log success
                db.add(LogEntry(level="INFO", source="Execution", message=f"Order Placed: {result.client_order_id}"))

                # 5. Broadcast via WebSocket
                await manager.broadcast({
                    "type": "ORDER_FILLED",
                    "data": {
                        "symbol": result.symbol,
                        "side": order_req.side,
                        "qty": result.qty,
                        "price": 0.0, # Result might not have fill price immediately if market order
                        "status": result.status,
                        "timestamp": str(datetime.now())
                    }
                })

            except RiskException as e:
                # Handled in RiskManager log
                pass
            except Exception as e:
                db.add(LogEntry(level="ERROR", source="Execution", message=f"Failed to process signal: {str(e)}"))
        
        await db.commit()

    def _calculate_quantity(self, signal: StrategySignal, account: AccountInfo) -> float:
        # Calculate Moving Average (Need historical data here?)
        # For now, we rely on metadata passed in the signal or we fetch it.
        # But wait, signal generation usually has access to indicators. 
        # Ideally, Signal object should contain indicators or context.
        # Let's assume signal.metadata has 'moving_avg' and 'current_price' derived from strategy. 
        # If not, we might need to fetch. 
        
        # NOTE: In mean_reversion.py, we put metadata={'max_price':..., 'drop_pct':...}.
        # We need to ensure Strategy passes 'moving_avg' and 'current_price'.
        
        # Fallback to 1.0 if missing data for now, but really we should fix Strategy to pass this.
        from app.services.position_sizer import PositionSizer
        
        # Extract from metadata
        current_price = signal.metadata.get('current_price', 0.0)
        moving_avg = signal.metadata.get('moving_avg', current_price) # avoid zero div if missing
        
        if current_price == 0:
            return 0.0 
            
        # Get params from signal metadata (passed from strategy)
        # Strategy should include these in signal metadata
        params = {
            'max_position_pct': signal.metadata.get('max_position_pct', 0.20),
            'scale_factor': signal.metadata.get('scale_factor', 200.0),
            'target_value': signal.metadata.get('target_value', 1000.0),
            'limit': signal.metadata.get('limit', 1000.0)
        }
        
        qty = PositionSizer.calculate_qty(
            current_price=current_price,
            moving_avg=moving_avg,
            portfolio_value=account.portfolio_value,
            buying_power=account.buying_power,
            params=params
        )
        
        return qty

    async def _log_signal(self, db: AsyncSession, signal: StrategySignal):
        log = SignalLog(
            strategy_name=signal.strategy_name,  # Use signal's strategy name
            symbol=signal.symbol,
            signal_type=signal.signal_type.name,
            signal_strength=signal.confidence,
            raw_data=signal.metadata
        )
        db.add(log)
