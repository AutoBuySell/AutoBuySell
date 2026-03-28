from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

import httpx
import asyncio
import random
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

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        cano: Optional[str] = None,
        acnt_prdt_cd: Optional[str] = None,
        base_url: Optional[str] = None,
        is_paper: Optional[bool] = None,
        **config,
    ):
        # Support legacy: fall back to settings if not provided
        if app_key is None:
            from app.core.config import settings as _s

            app_key = _s.KIS_APP_KEY
            app_secret = _s.KIS_APP_SECRET
            cano = _s.KIS_ACCOUNT_CANO
            acnt_prdt_cd = _s.KIS_ACCOUNT_ACNT_PRDT_CD
            base_url = _s.KIS_BASE_URL
            is_paper = _s.KIS_IS_PAPER

        self.base_url = (base_url or "https://openapi.koreainvestment.com:9443").rstrip(
            "/"
        )
        self.app_key = app_key
        self.app_secret = app_secret
        self.cano = cano
        self.acnt_prdt_cd = acnt_prdt_cd
        self.is_paper = is_paper if is_paper is not None else True

        if not all([self.app_key, self.app_secret, self.cano, self.acnt_prdt_cd]):
            raise ValueError(
                "KIS configuration missing. Set app_key, app_secret, cano, acnt_prdt_cd"
            )

        # Fixed policy: regular session only, with per-symbol exchange mapping (NASDAQ/NYSE)
        self.us_exchange = config.get("us_exchange", "NASD")
        self.us_exchanges = config.get("us_exchanges")
        if isinstance(self.us_exchanges, str):
            self.us_exchanges = [x.strip().upper() for x in self.us_exchanges.split(",") if x.strip()]
        if not self.us_exchanges:
            if str(self.us_exchange).upper() in {"ALL", "BOTH"}:
                self.us_exchanges = ["NASD", "NYSE"]
            else:
                self.us_exchanges = [str(self.us_exchange).upper()]
        self.us_price_excd = config.get("us_price_excd", "NAS")
        self.us_currency = config.get("us_currency", "USD")
        self._nyse_symbols = {"HIMS", "NET", "NIO", "OKLO", "TDOC"}

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._last_market_closed_reject_at: Optional[datetime] = None
        self._token_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_request_at: Optional[datetime] = None
        self._min_request_interval_sec = 0.35

    async def get_name(self) -> str:
        return "KIS OpenAPI (US)"

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if (
            self._access_token
            and self._token_expires_at
            and self._token_expires_at > now
        ):
            return self._access_token

        async with self._token_lock:
            # double-check after lock (another coroutine might have refreshed)
            now = datetime.now(timezone.utc)
            if (
                self._access_token
                and self._token_expires_at
                and self._token_expires_at > now
            ):
                return self._access_token

            url = f"{self.base_url}/oauth2/tokenP"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }

            last_err: Optional[Exception] = None
            for i in range(3):
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(url, json=payload)
                        if resp.status_code in (403, 429, 500, 502, 503, 504) and i < 2:
                            await asyncio.sleep((0.4 * (i + 1)) + random.uniform(0.05, 0.25))
                            continue
                        resp.raise_for_status()
                        data = resp.json()

                    token = data.get("access_token")
                    if not token:
                        raise RuntimeError(f"KIS token response missing access_token: {data}")

                    expires_in = int(data.get("expires_in", 3600))
                    self._access_token = token
                    self._token_expires_at = now + timedelta(seconds=max(expires_in - 60, 60))
                    return token
                except Exception as e:
                    last_err = e
                    if i < 2:
                        await asyncio.sleep((0.4 * (i + 1)) + random.uniform(0.05, 0.25))
                        continue

            raise RuntimeError(f"KIS token issuance failed after retries: {last_err}")

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
            async with self._request_lock:
                if self._last_request_at is not None:
                    elapsed = (
                        datetime.now(timezone.utc) - self._last_request_at
                    ).total_seconds()
                    if elapsed < self._min_request_interval_sec:
                        await asyncio.sleep(self._min_request_interval_sec - elapsed)
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json
                )
                self._last_request_at = datetime.now(timezone.utc)

            data = resp.json() if resp.text else {}
            last_resp, last_data = resp, data

            throttled = (
                isinstance(data, dict)
                and str(data.get("message", "")) in {"EGW00201", "EGW00133"}
            ) or (
                isinstance(data, dict)
                and "초당 거래건수를 초과" in str(data.get("msg1", ""))
            )
            transient_5xx = resp.status_code >= 500

            if (throttled or transient_5xx) and i < attempts - 1:
                await asyncio.sleep(1.2 * (i + 1))
                continue
            return resp, data

        return last_resp, last_data

    def _exchange_codes_for_symbol(self, symbol: str) -> tuple[str, str]:
        s = (symbol or "").upper()
        if s in self._nyse_symbols:
            return "NYSE", "NYS"
        return "NASD", "NAS"

    def _iter_us_exchanges(self) -> list[str]:
        # canonical, deduped order preserving
        seen = set()
        out = []
        for ex in (self.us_exchanges or [self.us_exchange]):
            e = str(ex).upper().strip()
            if not e or e in seen:
                continue
            seen.add(e)
            out.append(e)
        return out or ["NASD"]

    async def _fetch_us_price(self, symbol: str) -> float:
        _, price_excd = self._exchange_codes_for_symbol(symbol)
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        headers = await self._headers("HHDFS00000300")
        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": symbol,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        output = data.get("output") or {}
        return float(output.get("last", 0) or 0)

    async def get_account_info(self) -> AccountInfo:
        """
        Build account summary using multiple KIS endpoints for stable cash/equity fields.
        - inquire-balance: position valuation summary
        - inquire-present-balance: cash/deposit style fields
        - inquire-psamount: orderable amount snapshot (symbol-based)
        """
        bal_url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        bal_headers = await self._headers("VTTS3012R" if self.is_paper else "TTTS3012R")
        bal_params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self._iter_us_exchanges()[0],
            "TR_CRCY_CD": self.us_currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        present_url = (
            f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-present-balance"
        )
        present_headers = await self._headers(
            "VTRP6504R" if self.is_paper else "CTRP6504R"
        )
        present_params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "WCRC_FRCR_DVSN_CD": "01",
            "NATN_CD": "000",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00",
        }

        orderable_url = (
            f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        )
        orderable_headers = await self._headers(
            "VTTS3007R" if self.is_paper else "TTTS3007R"
        )
        # Symbol for probing orderable cash (safe large-cap default)
        probe_symbol = "AAPL"
        orderable_params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": self._exchange_codes_for_symbol(probe_symbol)[0],
            "OVRS_ORD_UNPR": "100",
            "ITEM_CD": probe_symbol,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            bal_resp, bal_data = await self._request_with_retry(
                client,
                "GET",
                bal_url,
                headers=bal_headers,
                params=bal_params,
                attempts=4,
            )
            if bal_resp.status_code >= 400:
                raise RuntimeError(
                    f"KIS account query failed: status={bal_resp.status_code}, body={bal_data}"
                )

            present_resp, present_data = await self._request_with_retry(
                client,
                "GET",
                present_url,
                headers=present_headers,
                params=present_params,
                attempts=4,
            )
            if present_resp.status_code >= 400:
                present_data = {}

            ord_resp, ord_data = await self._request_with_retry(
                client,
                "GET",
                orderable_url,
                headers=orderable_headers,
                params=orderable_params,
                attempts=3,
            )
            if ord_resp.status_code >= 400:
                ord_data = {}

        bal_out2 = bal_data.get("output2") or {}
        present_out2_list = present_data.get("output2") or []
        present_out2 = present_out2_list[0] if present_out2_list else {}
        present_out3 = present_data.get("output3") or {}
        ord_out = ord_data.get("output") or {}

        # Cash (prefer present-balance fields)
        cash = float(
            present_out2.get("frcr_dncl_amt_2", 0)
            or present_out3.get("tot_dncl_amt", 0)
            or bal_out2.get("frcr_dncl_amt_2", 0)
            or 0
        )

        buying_power = float(
            ord_out.get("ovrs_ord_psbl_amt", 0)
            or ord_out.get("ord_psbl_frcr_amt", 0)
            or present_out3.get("frcr_use_psbl_amt", 0)
            or bal_out2.get("ovrs_ord_psbl_amt", 0)
            or cash
        )

        # Position valuation from per-exchange positions (covers NASD + NYSE if configured).
        position_value = sum(float(p.market_value or 0.0) for p in (await self.get_positions()))

        # In paper responses cash fields are sometimes zero while orderable cash is populated.
        effective_cash = cash if cash > 0 else (buying_power if buying_power > 0 else 0.0)
        cash = effective_cash
        portfolio = position_value + effective_cash

        return AccountInfo(
            account_id=f"{self.cano}-{self.acnt_prdt_cd}",
            currency=self.us_currency,
            cash=cash,
            portfolio_value=portfolio,
            buying_power=buying_power,
            is_paper=self.is_paper,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = await self._headers("VTTS3012R" if self.is_paper else "TTTS3012R")

        merged: dict[str, BrokerPosition] = {}

        async with httpx.AsyncClient(timeout=15.0) as client:
            for exch in self._iter_us_exchanges():
                params = {
                    "CANO": self.cano,
                    "ACNT_PRDT_CD": self.acnt_prdt_cd,
                    "OVRS_EXCG_CD": exch,
                    "TR_CRCY_CD": self.us_currency,
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                }
                resp, data = await self._request_with_retry(
                    client, "GET", url, headers=headers, params=params, attempts=4
                )
                if resp.status_code >= 400:
                    continue

                for row in data.get("output1", []):
                    qty = float(row.get("ovrs_cblc_qty", 0) or 0)
                    if qty <= 0:
                        continue

                    symbol = str(row.get("ovrs_pdno", ""))
                    if not symbol:
                        continue
                    avg_price = float(row.get("pchs_avg_pric", 0) or 0)
                    cur_price = float(row.get("now_pric2", 0) or 0)
                    if cur_price <= 0 and symbol:
                        try:
                            cur_price = await self._fetch_us_price(symbol)
                        except Exception:
                            cur_price = 0

                    market_value = float(
                        row.get("frcr_evlu_amt2", qty * cur_price) or qty * cur_price
                    )
                    unrealized_pl = float(row.get("evlu_pfls_amt2", 0) or 0)
                    base = qty * avg_price if avg_price > 0 else 0
                    unrealized_plpc = (unrealized_pl / base) if base > 0 else 0.0

                    merged[symbol] = BrokerPosition(
                        symbol=symbol,
                        qty=qty,
                        avg_entry_price=avg_price,
                        current_price=cur_price,
                        market_value=market_value,
                        unrealized_pl=unrealized_pl,
                        unrealized_plpc=unrealized_plpc,
                        side="long",
                    )

        return list(merged.values())

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        # US overseas order endpoint
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"

        is_buy = order.side.lower() == "buy"
        if self.is_paper:
            tr_id = "VTTT1002U" if is_buy else "VTTT1006U"
        else:
            tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        price = order.limit_price
        if order.type.lower() == "market" or not price:
            # KIS demo for US commonly supports limit order code("00").
            # Use current price as synthetic limit for phase 1 compatibility.
            price = await self._fetch_us_price(order.symbol)
            if price <= 0:
                raise RuntimeError(
                    f"Failed to resolve current price for {order.symbol}"
                )

        order_excg, _ = self._exchange_codes_for_symbol(order.symbol)
        payload = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": order_excg,
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
            resp, data = await self._request_with_retry(
                client, "POST", url, headers=headers, json=payload
            )

            if resp.status_code >= 400:
                raise RuntimeError(
                    f"KIS order failed: status={resp.status_code}, body={data}"
                )

            rt_cd = str(data.get("rt_cd", "")) if isinstance(data, dict) else ""
            if rt_cd and rt_cd != "0":
                msg1 = str(data.get("msg1", "")) if isinstance(data, dict) else ""
                if "장시작전" in msg1 or "장마감" in msg1 or "장운영" in msg1:
                    self._last_market_closed_reject_at = datetime.now(timezone.utc)
                raise RuntimeError(
                    f"KIS order rejected: rt_cd={rt_cd}, msg1={msg1}, body={data}"
                )

        output = data.get("output", {}) if isinstance(data, dict) else {}
        ord_no = str(output.get("ODNO", ""))
        rt_cd = str(data.get("rt_cd", "")) if isinstance(data, dict) else ""
        msg1 = str(data.get("msg1", "")) if isinstance(data, dict) else ""

        if rt_cd and rt_cd != "0":
            if "장시작전" in msg1 or "장마감" in msg1 or "장운영" in msg1:
                self._last_market_closed_reject_at = datetime.now(timezone.utc)
            raise RuntimeError(
                f"KIS order rejected: rt_cd={rt_cd}, msg1={msg1}, body={data}"
            )

        return OrderResult(
            client_order_id=ord_no or f"kis-{datetime.now().timestamp()}",
            broker_order_id=ord_no,
            status="accepted",
            symbol=order.symbol,
            qty=order.qty,
        )

    async def cancel_order(self, order_id: str) -> bool:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order-rvsecncl"
        tr_id = "VTTT1004U" if self.is_paper else "TTTT1004U"
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
            resp, data = await self._request_with_retry(
                client, "POST", url, headers=headers, json=payload
            )
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

    async def get_historicals(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Any]:
        tf = timeframe.lower()
        _, price_excd = self._exchange_codes_for_symbol(symbol)

        # Daily candles
        if tf in {"1d", "1day"}:
            url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
            headers = await self._headers("HHDFS76240000")
            today = datetime.now().strftime("%Y%m%d")
            params = {
                "AUTH": "",
                "EXCD": price_excd,
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": today,
                "MODP": "1",
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp, data = await self._request_with_retry(
                    client, "GET", url, headers=headers, params=params
                )
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
            "1min": "1",
            "1m": "1",
            "5min": "5",
            "5m": "5",
            "15min": "15",
            "15m": "15",
            "30min": "30",
            "30m": "30",
            "1hour": "60",
            "1h": "60",
        }
        nmin = minute_map.get(tf)
        if not nmin:
            return []

        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        headers = await self._headers("HHDFS76950200")
        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": symbol,
            "NMIN": nmin,
            "PINC": "1",
            "NEXT": "",
            "NREC": str(min(max(limit, 1), 120)),
            "FILL": "",
            "KEYB": "",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(
                client, "GET", url, headers=headers, params=params
            )
            if resp.status_code >= 400:
                return []

        rows = data.get("output2", [])
        bars: List[SimpleBar] = []
        kst = ZoneInfo("Asia/Seoul")
        for row in rows[:limit]:
            # KIS 해외분봉 returns both exchange(local) and KST fields.
            # Use KST fields to avoid ambiguous timezone conversion.
            dt = row.get("kymd") or row.get("xymd")
            hm = row.get("khms") or row.get("xhms")
            if not dt or not hm:
                continue
            ts_local = datetime.strptime(f"{dt}{hm}", "%Y%m%d%H%M%S").replace(
                tzinfo=kst
            )
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

    async def get_portfolio_history(
        self, period: str = "1M", timeframe: str = "1D"
    ) -> PortfolioHistory:
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
        """
        Safe continuous KIS fills fetch:
        - fixed interval (5s)
        - derive next window from latest ord_dt
        - stop on empty/no-forward-progress/failure
        """
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-ccnl"
        tr_id = "VTTS3035R" if self.is_paper else "TTTS3035R"

        kst_today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        # Sync window default: last 30 days (lighter periodic sync load)
        start_date = kst_today - timedelta(days=30)

        def parse_rows(rows: list[dict], end_dt_str: str) -> List[TradeFill]:
            parsed: List[TradeFill] = []
            for row in rows:
                ccld_qty = float(row.get("ft_ccld_qty", 0) or 0)
                if ccld_qty <= 0:
                    continue

                ord_no = str(row.get("odno", "") or "")
                ccld_no = str(row.get("ovrs_ccld_no", "") or "")
                sym = str(
                    row.get("ovrs_pdno")
                    or row.get("pdno")
                    or row.get("ovrs_pd_name")
                    or row.get("ovrs_item_name")
                    or row.get("pd_name")
                    or row.get("item_name")
                    or row.get("prdt_name")
                    or ""
                )

                side_code = str(row.get("sll_buy_dvsn_cd", "") or "").strip()
                side_name = str(row.get("sll_buy_dvsn_name", "") or "").lower()
                if side_code == "02" or "매수" in side_name or side_name == "buy":
                    side = "buy"
                elif side_code == "01" or "매도" in side_name or side_name == "sell":
                    side = "sell"
                else:
                    side = "buy"

                price = float(
                    row.get("ft_ccld_unpr3", 0) or row.get("ft_ord_unpr3", 0) or 0
                )

                ord_dt = str(row.get("ord_dt", "") or end_dt_str)
                ord_tm = str(row.get("ord_tmd", "") or "000000").zfill(6)
                try:
                    executed_local = datetime.strptime(
                        f"{ord_dt}{ord_tm}", "%Y%m%d%H%M%S"
                    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
                    executed_at = executed_local.astimezone(timezone.utc)
                except Exception:
                    executed_at = datetime.now(timezone.utc)

                parsed.append(
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
            return parsed

        fills: List[TradeFill] = []
        seen_execution_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=20.0) as client:
            while len(fills) < limit:
                headers = await self._headers(tr_id)
                end_dt = kst_today.strftime("%Y%m%d")
                params = {
                    "CANO": self.cano,
                    "ACNT_PRDT_CD": self.acnt_prdt_cd,
                    "PDNO": "",
                    "ORD_STRT_DT": start_date.strftime("%Y%m%d"),
                    "ORD_END_DT": end_dt,
                    "SLL_BUY_DVSN": "00",
                    "CCLD_NCCS_DVSN": "00",
                    "OVRS_EXCG_CD": "",
                    "SORT_SQN": "DS",
                    "ORD_DT": "",
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "CTX_AREA_NK200": "",
                    "CTX_AREA_FK200": "",
                }

                resp, data = await self._request_with_retry(
                    client, "GET", url, headers=headers, params=params
                )
                if resp.status_code >= 400:
                    break
                rt_cd = str((data or {}).get("rt_cd", ""))
                if rt_cd and rt_cd != "0":
                    break

                rows = (data or {}).get("output") or []
                if not rows:
                    break

                parsed = parse_rows(rows, end_dt)
                for f in parsed:
                    if f.execution_id in seen_execution_ids:
                        continue
                    seen_execution_ids.add(f.execution_id)
                    fills.append(f)
                    if len(fills) >= limit:
                        break

                max_dt_str = max(
                    (str(r.get("ord_dt", "")) for r in rows if r.get("ord_dt")),
                    default="",
                )
                if not max_dt_str:
                    break

                next_start = datetime.strptime(max_dt_str, "%Y%m%d").date() + timedelta(days=1)
                if next_start <= start_date or next_start > kst_today:
                    break

                start_date = next_start
                await asyncio.sleep(5)

        fills.sort(key=lambda x: x.executed_at, reverse=True)
        return fills[:limit]
