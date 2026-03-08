from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

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
    """KIS Open API adapter (phase 1: basic domestic stock support)."""

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

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def get_name(self) -> str:
        return "KIS OpenAPI"

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

        # KIS usually returns expires_in seconds
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

    async def get_account_info(self) -> AccountInfo:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        headers = await self._headers("VTTC8434R" if settings.KIS_IS_PAPER else "TTTC8434R")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        out2 = (data.get("output2") or [{}])[0]
        cash = float(out2.get("dnca_tot_amt", 0) or 0)
        portfolio = float(out2.get("tot_evlu_amt", 0) or 0)
        buying_power = float(out2.get("ord_psbl_cash", cash) or cash)

        return AccountInfo(
            account_id=f"{self.cano}-{self.acnt_prdt_cd}",
            currency="KRW",
            cash=cash,
            portfolio_value=portfolio,
            buying_power=buying_power,
            is_paper=settings.KIS_IS_PAPER,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        headers = await self._headers("VTTC8434R" if settings.KIS_IS_PAPER else "TTTC8434R")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        positions: List[BrokerPosition] = []
        for row in data.get("output1", []):
            qty = float(row.get("hldg_qty", 0) or 0)
            if qty <= 0:
                continue
            avg_price = float(row.get("pchs_avg_pric", 0) or 0)
            cur_price = float(row.get("prpr", 0) or 0)
            market_value = float(row.get("evlu_amt", qty * cur_price) or qty * cur_price)
            unrealized_pl = float(row.get("evlu_pfls_amt", 0) or 0)
            base = qty * avg_price if avg_price > 0 else 0
            unrealized_plpc = (unrealized_pl / base) if base > 0 else 0.0
            positions.append(
                BrokerPosition(
                    symbol=str(row.get("pdno", "")),
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
        # KIS domestic cash order endpoint (phase 1)
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "VTTC0802U" if order.side.lower() == "buy" else "VTTC0801U"
        if not settings.KIS_IS_PAPER:
            tr_id = "TTTC0802U" if order.side.lower() == "buy" else "TTTC0801U"

        payload = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": order.symbol,
            "ORD_DVSN": "01" if order.type.lower() == "market" else "00",
            "ORD_QTY": str(int(order.qty)),
            "ORD_UNPR": "0" if order.type.lower() == "market" else str(order.limit_price or 0),
        }
        headers = await self._headers(tr_id)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        output = data.get("output", {})
        ord_no = str(output.get("ODNO", ""))

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
        # Phase 1 simple market gate for KR market hours (Mon-Fri, 09:00-15:30 KST)
        now_kst = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul"))
        if now_kst.weekday() >= 5:
            return False
        hhmm = now_kst.hour * 100 + now_kst.minute
        return 900 <= hhmm <= 1530

    async def get_historicals(self, symbol: str, timeframe: str, limit: int) -> List[Any]:
        # KIS daily candle endpoint support in phase 1 (1d only)
        tf = timeframe.lower()
        if tf not in {"1d", "1day"}:
            return []

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": "20000101",
            "FID_INPUT_DATE_2": today,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        headers = await self._headers("FHKST03010100")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        bars: List[SimpleBar] = []
        rows = data.get("output2", [])[:limit]
        for row in rows:
            dt = row.get("stck_bsop_date")
            if not dt:
                continue
            ts = datetime.strptime(dt, "%Y%m%d").replace(tzinfo=timezone.utc)
            bars.append(
                SimpleBar(
                    timestamp=ts,
                    open=float(row.get("stck_oprc", 0) or 0),
                    high=float(row.get("stck_hgpr", 0) or 0),
                    low=float(row.get("stck_lwpr", 0) or 0),
                    close=float(row.get("stck_clpr", 0) or 0),
                    volume=float(row.get("acml_vol", 0) or 0),
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

