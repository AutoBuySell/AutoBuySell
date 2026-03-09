from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

import httpx

from app.brokers.base import (
    BrokerAdapter,
    AccountInfo,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    PortfolioHistory,
    TradeFill,
)
from app.core.config import settings


@dataclass
class SimpleBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class KISBroker(BrokerAdapter):
    """KIS Open API adapter (phase 1: US stock flow for parity with Alpaca setup)."""

    def __init__(self):
        self.base_url = settings.KIS_BASE_URL.rstrip("/")
        self.app_key = settings.KIS_APP_KEY
        self.app_secret = settings.KIS_APP_SECRET
        self.cano = settings.KIS_ACCOUNT_CANO
        self.acnt_prdt_cd = settings.KIS_ACCOUNT_ACNT_PRDT_CD

        if not all([self.app_key, self.app_secret, self.cano, self.acnt_prdt_cd]):
            raise ValueError(
                "KIS configuration missing. Set KIS_APP_KEY, KIS_APP_SECRET, "
                "KIS_ACCOUNT_CANO, KIS_ACCOUNT_ACNT_PRDT_CD"
            )

        # US market defaults (can override via env)
        self.us_exchange = settings.KIS_US_EXCHANGE  # NASD/NYSE/AMEX
        self.us_price_excd = settings.KIS_US_PRICE_EXCD  # NAS/NYS/AMS
        self.us_currency = settings.KIS_US_CURRENCY

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def get_name(self) -> str:
        return "KIS OpenAPI (US)"

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at and self._token_expires_at > now:
            return self._access_token

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"KIS token response missing access_token: {data}")

        expires_in = int(data.get("expires_in", 3600))
        self._access_token = token
        self._token_expires_at = now + timedelta(seconds=max(expires_in - 60, 60))
        return token

    async def _headers(self, tr_id: str) -> dict:
        token = await self._ensure_token()
        return {
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    async def _fetch_us_price(self, symbol: str) -> float:
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        headers = await self._headers("HHDFS00000300")
        params = {
            "AUTH": "",
            "EXCD": self.us_price_excd,
            "SYMB": symbol,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        output = data.get("output") or {}
        return float(output.get("last", 0) or 0)

    async def get_account_info(self) -> AccountInfo:
        # US overseas balance query
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = await self._headers("VTTS3012R" if settings.KIS_IS_PAPER else "TTTS3012R")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self.us_exchange,
            "TR_CRCY_CD": self.us_currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        output2 = data.get("output2") or {}
        cash = float(output2.get("frcr_dncl_amt_2", 0) or 0)
        portfolio = float(output2.get("tot_evlu_pfls_amt", 0) or 0)
        buying_power = float(output2.get("ovrs_ord_psbl_amt", cash) or cash)

        return AccountInfo(
            account_id=f"{self.cano}-{self.acnt_prdt_cd}",
            currency=self.us_currency,
            cash=cash,
            portfolio_value=portfolio,
            buying_power=buying_power,
            is_paper=settings.KIS_IS_PAPER,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = await self._headers("VTTS3012R" if settings.KIS_IS_PAPER else "TTTS3012R")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self.us_exchange,
            "TR_CRCY_CD": self.us_currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        positions: List[BrokerPosition] = []
        for row in data.get("output1", []):
            qty = float(row.get("ovrs_cblc_qty", 0) or 0)
            if qty <= 0:
                continue

            symbol = str(row.get("ovrs_pdno", ""))
            avg_price = float(row.get("pchs_avg_pric", 0) or 0)
            cur_price = float(row.get("now_pric2", 0) or 0)
            if cur_price <= 0 and symbol:
                try:
                    cur_price = await self._fetch_us_price(symbol)
                except Exception:
                    cur_price = 0

            market_value = float(row.get("frcr_evlu_amt2", qty * cur_price) or qty * cur_price)
            unrealized_pl = float(row.get("evlu_pfls_amt2", 0) or 0)
            base = qty * avg_price if avg_price > 0 else 0
            unrealized_plpc = (unrealized_pl / base) if base > 0 else 0.0

            positions.append(
                BrokerPosition(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=avg_price,
                    current_price=cur_price,
                    market_value=market_value,
                    unrealized_pl=unrealized_pl,
                    unrealized_plpc=unrealized_plpc,
                    side="long",
                )
            )
        return positions

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        # US overseas order endpoint
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"

        is_buy = order.side.lower() == "buy"
        if settings.KIS_IS_PAPER:
            tr_id = "VTTT1002U" if is_buy else "VTTT1006U"
        else:
            tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        price = order.limit_price
        if order.type.lower() == "market" or not price:
            # KIS demo for US commonly supports limit order code("00").
            # Use current price as synthetic limit for phase 1 compatibility.
            price = await self._fetch_us_price(order.symbol)
            if price <= 0:
                raise RuntimeError(f"Failed to resolve current price for {order.symbol}")

        payload = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self.us_exchange,
            "PDNO": order.symbol,
            "ORD_QTY": str(int(order.qty)),
            "OVRS_ORD_UNPR": f"{price:.4f}",
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "SLL_TYPE": "" if is_buy else "00",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 지정가
        }
        headers = await self._headers(tr_id)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json() if resp.text else {}

            # Fallback: try US daytime-order endpoint when generic order endpoint fails
            if resp.status_code >= 400:
                daytime_url = f"{self.base_url}/uapi/overseas-stock/v1/trading/daytime-order"
                daytime_tr = "TTTS6036U" if is_buy else "TTTS6037U"
                daytime_headers = await self._headers(daytime_tr)
                daytime_payload = {
                    "CANO": self.cano,
                    "ACNT_PRDT_CD": self.acnt_prdt_cd,
                    "OVRS_EXCG_CD": self.us_exchange,
                    "PDNO": order.symbol,
                    "ORD_QTY": str(int(order.qty)),
                    "OVRS_ORD_UNPR": f"{price:.4f}",
                    "CTAC_TLNO": "",
                    "MGCO_APTM_ODNO": "",
                    "ORD_SVR_DVSN_CD": "0",
                    "ORD_DVSN": "00",
                }
                resp2 = await client.post(daytime_url, headers=daytime_headers, json=daytime_payload)
                data2 = resp2.json() if resp2.text else {}
                if resp2.status_code >= 400:
                    raise RuntimeError(
                        f"KIS order failed. order_status={resp.status_code}, order_body={data}, "
                        f"daytime_status={resp2.status_code}, daytime_body={data2}"
                    )
                data = data2

        output = data.get("output", {}) if isinstance(data, dict) else {}
        ord_no = str(output.get("ODNO", ""))
        rt_cd = str(data.get("rt_cd", "")) if isinstance(data, dict) else ""
        msg1 = str(data.get("msg1", "")) if isinstance(data, dict) else ""

        if rt_cd and rt_cd != "0":
            raise RuntimeError(f"KIS order rejected: rt_cd={rt_cd}, msg1={msg1}, body={data}")

        return OrderResult(
            client_order_id=ord_no or f"kis-{datetime.now().timestamp()}",
            broker_order_id=ord_no,
            status="accepted",
            symbol=order.symbol,
            qty=order.qty,
        )

    async def cancel_order(self, order_id: str) -> bool:
        # Not implemented in phase 1
        return False

    async def get_market_status(self) -> bool:
        # For US flow, avoid local time hard-blocking. Let broker/rejections decide final executability.
        return True

    async def get_historicals(self, symbol: str, timeframe: str, limit: int) -> List[Any]:
        # US daily price endpoint. (KIS daily API)
        tf = timeframe.lower()
        if tf not in {"1d", "1day"}:
            return []

        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        headers = await self._headers("HHDFS76240000")
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "AUTH": "",
            "EXCD": self.us_price_excd,
            "SYMB": symbol,
            "GUBN": "0",  # 일봉
            "BYMD": today,
            "MODP": "1",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        rows = data.get("output2", [])
        bars: List[SimpleBar] = []
        for row in rows[:limit]:
            dt = row.get("xymd")
            if not dt:
                continue
            ts = datetime.strptime(dt, "%Y%m%d").replace(tzinfo=timezone.utc)
            bars.append(
                SimpleBar(
                    timestamp=ts,
                    open=float(row.get("open", 0) or 0),
                    high=float(row.get("high", 0) or 0),
                    low=float(row.get("low", 0) or 0),
                    close=float(row.get("clos", 0) or 0),
                    volume=float(row.get("tvol", 0) or 0),
                )
            )

        bars.reverse()  # oldest -> newest
        return bars

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> PortfolioHistory:
        # Not provided in a single normalized endpoint by KIS; phase 1 returns empty.
        return PortfolioHistory(timestamp=[], equity=[], profit_loss=[], profit_loss_pct=[], timeframe=timeframe)

    async def get_trade_fills(self, limit: int = 100) -> List[TradeFill]:
        # Phase 1: trade fill sync not implemented yet for KIS
        return []
