from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

import httpx
import asyncio
from zoneinfo import ZoneInfo
from datetime import time as dtime

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

        # Fixed policy for now: NASDAQ regular session only (no daytime/pre/post)
        self.us_exchange = "NASD"
        self.us_price_excd = "NAS"
        self.us_currency = settings.KIS_US_CURRENCY

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._last_market_closed_reject_at: Optional[datetime] = None

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

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        attempts: int = 3,
    ) -> tuple[httpx.Response, dict]:
        last_resp = None
        last_data = {}
        for i in range(attempts):
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            data = resp.json() if resp.text else {}
            last_resp, last_data = resp, data

            throttled = (
                (isinstance(data, dict) and str(data.get("message", "")) in {"EGW00201", "EGW00133"})
                or (isinstance(data, dict) and "초당 거래건수를 초과" in str(data.get("msg1", "")))
            )
            if throttled and i < attempts - 1:
                await asyncio.sleep(1.2 * (i + 1))
                continue
            return resp, data

        return last_resp, last_data

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
            resp, data = await self._request_with_retry(client, "POST", url, headers=headers, json=payload)

            if resp.status_code >= 400:
                raise RuntimeError(f"KIS order failed: status={resp.status_code}, body={data}")

            rt_cd = str(data.get("rt_cd", "")) if isinstance(data, dict) else ""
            if rt_cd and rt_cd != "0":
                msg1 = str(data.get("msg1", "")) if isinstance(data, dict) else ""
                if "장시작전" in msg1 or "장마감" in msg1 or "장운영" in msg1:
                    self._last_market_closed_reject_at = datetime.now(timezone.utc)
                raise RuntimeError(f"KIS order rejected: rt_cd={rt_cd}, msg1={msg1}, body={data}")


        output = data.get("output", {}) if isinstance(data, dict) else {}
        ord_no = str(output.get("ODNO", ""))
        rt_cd = str(data.get("rt_cd", "")) if isinstance(data, dict) else ""
        msg1 = str(data.get("msg1", "")) if isinstance(data, dict) else ""

        if rt_cd and rt_cd != "0":
            if "장시작전" in msg1 or "장마감" in msg1 or "장운영" in msg1:
                self._last_market_closed_reject_at = datetime.now(timezone.utc)
            raise RuntimeError(f"KIS order rejected: rt_cd={rt_cd}, msg1={msg1}, body={data}")

        return OrderResult(
            client_order_id=ord_no or f"kis-{datetime.now().timestamp()}",
            broker_order_id=ord_no,
            status="accepted",
            symbol=order.symbol,
            qty=order.qty,
        )

    async def cancel_order(self, order_id: str) -> bool:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order-rvsecncl"
        tr_id = "VTTT1004U" if settings.KIS_IS_PAPER else "TTTT1004U"
        headers = await self._headers(tr_id)
        payload = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self.us_exchange,
            "ORGN_ODNO": order_id,
            "RVSE_CNCL_DVSN_CD": "02",  # cancel
            "ORD_QTY": "0",
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp, data = await self._request_with_retry(client, "POST", url, headers=headers, json=payload)
        if resp.status_code >= 400:
            return False
        return str((data or {}).get("rt_cd", "1")) == "0"

    async def get_market_status(self) -> bool:
        # 1) Time rule + DST (America/New_York regular session only)
        now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5:
            return False
        regular_open = dtime(9, 30)
        regular_close = dtime(16, 0)
        if not (regular_open <= now_et.time() <= regular_close):
            return False

        # 2) Indirect check: quote endpoint should respond during tradable window
        try:
            _ = await self._fetch_us_price("AAPL")
        except Exception:
            return False

        # 3) Order response code memory check (recent market-closed reject)
        if self._last_market_closed_reject_at:
            age = datetime.now(timezone.utc) - self._last_market_closed_reject_at
            if age.total_seconds() < 180:
                return False

        return True

    async def get_historicals(self, symbol: str, timeframe: str, limit: int) -> List[Any]:
        tf = timeframe.lower()

        # Daily candles
        if tf in {"1d", "1day"}:
            url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
            headers = await self._headers("HHDFS76240000")
            today = datetime.now().strftime("%Y%m%d")
            params = {
                "AUTH": "",
                "EXCD": self.us_price_excd,
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": today,
                "MODP": "1",
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
                if resp.status_code >= 400:
                    return []

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
            bars.reverse()
            return bars

        # Intraday minute candles (supports 1m/5m/15m/30m/1h)
        minute_map = {
            "1min": "1", "1m": "1",
            "5min": "5", "5m": "5",
            "15min": "15", "15m": "15",
            "30min": "30", "30m": "30",
            "1hour": "60", "1h": "60",
        }
        nmin = minute_map.get(tf)
        if not nmin:
            return []

        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        headers = await self._headers("HHDFS76950200")
        params = {
            "AUTH": "",
            "EXCD": self.us_price_excd,
            "SYMB": symbol,
            "NMIN": nmin,
            "PINC": "1",
            "NEXT": "",
            "NREC": str(min(max(limit, 1), 120)),
            "FILL": "",
            "KEYB": "",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
            if resp.status_code >= 400:
                return []

        rows = data.get("output2", [])
        bars: List[SimpleBar] = []
        kst = ZoneInfo("Asia/Seoul")
        for row in rows[:limit]:
            dt = row.get("xymd")
            hm = row.get("xhms")
            if not dt or not hm:
                continue
            # xhms ex) 100000 (exchange local). samples are aligned to KST view for US market.
            ts_local = datetime.strptime(f"{dt}{hm}", "%Y%m%d%H%M%S").replace(tzinfo=kst)
            ts = ts_local.astimezone(timezone.utc)
            bars.append(
                SimpleBar(
                    timestamp=ts,
                    open=float(row.get("open", 0) or 0),
                    high=float(row.get("high", 0) or 0),
                    low=float(row.get("low", 0) or 0),
                    close=float(row.get("last", 0) or 0),
                    volume=float(row.get("evol", 0) or 0),
                )
            )

        bars.reverse()
        return bars

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> PortfolioHistory:
        """
        KIS does not expose Alpaca-style equity curve in one normalized endpoint.
        Phase-1 compatibility: return a synthetic flat curve based on current portfolio value
        so UI/API contracts remain compatible.
        """
        account = await self.get_account_info()

        tf = timeframe.upper()
        period_key = period.upper()

        # point counts by period (reasonable defaults)
        period_points = {
            "1D": 24,
            "5D": 40,
            "1W": 40,
            "1M": 30,
            "3M": 60,
            "6M": 120,
            "1A": 180,
            "1Y": 180,
        }
        points = period_points.get(period_key, 30)

        # step seconds by timeframe
        step_map = {
            "1MIN": 60,
            "5MIN": 300,
            "15MIN": 900,
            "30MIN": 1800,
            "1H": 3600,
            "1D": 86400,
        }
        step = step_map.get(tf, 86400)

        now = int(datetime.now(timezone.utc).timestamp())
        start = now - step * (points - 1)
        ts = [start + i * step for i in range(points)]

        equity_value = float(account.portfolio_value or 0.0)
        equity = [equity_value for _ in range(points)]
        pnl = [0.0 for _ in range(points)]
        pnl_pct = [0.0 for _ in range(points)]

        return PortfolioHistory(
            timestamp=ts,
            equity=equity,
            profit_loss=pnl,
            profit_loss_pct=pnl_pct,
            timeframe=timeframe,
        )

    async def get_trade_fills(self, limit: int = 100) -> List[TradeFill]:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-ccnl"
        tr_id = "VTTS3035R" if settings.KIS_IS_PAPER else "TTTS3035R"
        headers = await self._headers(tr_id)

        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": "",
            "ORD_STRT_DT": today,
            "ORD_END_DT": today,
            "SLL_BUY_DVSN": "00",
            "CCLD_NCCS_DVSN": "00",  # demo only supports 00
            "OVRS_EXCG_CD": "",
            "SORT_SQN": "DS",
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CTX_AREA_NK200": "",
            "CTX_AREA_FK200": "",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
            if resp.status_code >= 400:
                return []

        rows = data.get("output") or []
        fills: List[TradeFill] = []
        for row in rows[:limit]:
            ccld_qty = float(row.get("ft_ccld_qty", 0) or 0)
            if ccld_qty <= 0:
                continue
            ord_no = str(row.get("odno", "") or "")
            ccld_no = str(row.get("ovrs_ccld_no", "") or "")
            sym = str(row.get("ovrs_pdno", "") or "")

            side_code = str(row.get("sll_buy_dvsn_cd", "") or "").strip()
            side_name = str(row.get("sll_buy_dvsn_name", "") or "").lower()
            if side_code == "02" or "매수" in side_name or side_name == "buy":
                side = "buy"
            elif side_code == "01" or "매도" in side_name or side_name == "sell":
                side = "sell"
            else:
                side = "buy"

            price = float(row.get("ft_ccld_unpr3", 0) or row.get("ft_ord_unpr3", 0) or 0)

            # timestamp parse: KIS date/time fields are usually in Korea local time
            ord_dt = str(row.get("ord_dt", "") or today)
            ord_tm = str(row.get("ord_tmd", "") or "000000").zfill(6)
            try:
                executed_local = datetime.strptime(f"{ord_dt}{ord_tm}", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Seoul"))
                executed_at = executed_local.astimezone(timezone.utc)
            except Exception:
                executed_at = datetime.now(timezone.utc)

            fills.append(
                TradeFill(
                    execution_id=ccld_no or f"{ord_no}-{sym}-{ord_dt}{ord_tm}",
                    order_id=ord_no or None,
                    symbol=sym,
                    side=side,
                    qty=ccld_qty,
                    price=price,
                    commission=0.0,
                    executed_at=executed_at,
                )
            )

        return fills
