from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo
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


@dataclass
class SimpleBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class KISKRBroker(BrokerAdapter):
    """KIS Domestic (KRX) broker adapter (paper/live)."""

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
        self.base_url = (base_url or "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.cano = cano
        self.acnt_prdt_cd = acnt_prdt_cd
        self.is_paper = is_paper if is_paper is not None else True

        if not all([self.app_key, self.app_secret, self.cano, self.acnt_prdt_cd]):
            raise ValueError("KISKR configuration missing app_key/app_secret/cano/acnt_prdt_cd")

        self.currency = "KRW"
        self.market = str(config.get("market", "KRX")).upper()

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._token_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_request_at: Optional[datetime] = None
        # KIS guidance is stricter on paper accounts; keep conservative defaults.
        self._min_request_interval_sec = 0.5 if self.is_paper else 0.1
        self._request_jitter_sec = 0.03

    async def get_name(self) -> str:
        return "KIS OpenAPI (KR)"

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at and self._token_expires_at > now:
            return self._access_token

        async with self._token_lock:
            # double-check after acquiring lock (another coroutine may have refreshed)
            now = datetime.now(timezone.utc)
            if self._access_token and self._token_expires_at and self._token_expires_at > now:
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
                            # startup burst/rate-limit guard with jittered backoff
                            await asyncio.sleep((0.4 * (i + 1)) + random.uniform(0.05, 0.25))
                            continue
                        resp.raise_for_status()
                        data = resp.json()

                    token = data.get("access_token")
                    if not token:
                        raise RuntimeError(f"KISKR token response missing access_token: {data}")

                    expires_in = int(data.get("expires_in", 3600))
                    self._access_token = token
                    self._token_expires_at = now + timedelta(seconds=max(expires_in - 60, 60))
                    return token
                except Exception as e:
                    last_err = e
                    if i < 2:
                        await asyncio.sleep((0.4 * (i + 1)) + random.uniform(0.05, 0.25))
                        continue

            raise RuntimeError(f"KISKR token issuance failed after retries: {last_err}")

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
            try:
                async with self._request_lock:
                    if self._last_request_at is not None:
                        elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
                        if elapsed < self._min_request_interval_sec:
                            await asyncio.sleep(
                                (self._min_request_interval_sec - elapsed)
                                + random.uniform(0.0, self._request_jitter_sec)
                            )
                    resp = await client.request(method, url, headers=headers, params=params, json=json)
                    self._last_request_at = datetime.now(timezone.utc)
            except (httpx.TransportError, httpx.ReadTimeout, httpx.ConnectTimeout):
                if i < attempts - 1:
                    await asyncio.sleep((0.4 * (i + 1)) + random.uniform(0.05, 0.25))
                    continue
                raise

            data = resp.json() if resp.text else {}
            last_resp, last_data = resp, data
            throttled = (
                isinstance(data, dict)
                and (
                    str(data.get("message", "")) in {"EGW00201", "EGW00133"}
                    or str(data.get("msg_cd", "")) in {"EGW00201", "EGW00133"}
                )
            ) or (
                isinstance(data, dict)
                and "초당 거래건수를 초과" in str(data.get("msg1", ""))
            ) or resp.status_code == 429

            transient_5xx = resp.status_code >= 500

            if (throttled or transient_5xx) and i < attempts - 1:
                await asyncio.sleep((1.1 * (i + 1)) + random.uniform(0.05, 0.25))
                continue
            return resp, data

        return last_resp, last_data

    async def _fetch_kr_price(self, symbol: str) -> float:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = await self._headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        out = data.get("output") or {}
        return float(out.get("stck_prpr", 0) or 0)

    async def get_account_info(self) -> AccountInfo:
        positions = await self.get_positions()
        position_value = sum(float(p.market_value or 0.0) for p in positions)

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = await self._headers("VTTC8908R" if self.is_paper else "TTTC8908R")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": "005930",  # probe symbol (Samsung)
            "ORD_UNPR": "1",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }

        cash = 0.0
        buying_power = 0.0
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
            if resp.status_code < 400:
                out = data.get("output") or {}
                cash = float(out.get("ord_psbl_cash", 0) or out.get("dnca_tot_amt", 0) or 0)
                buying_power = float(out.get("ord_psbl_cash", 0) or 0)
        except Exception:
            pass

        portfolio = (cash if cash > 0 else 0.0) + position_value
        if portfolio <= 0 and buying_power > 0:
            portfolio = buying_power
            cash = max(cash, buying_power)

        return AccountInfo(
            account_id=f"{self.cano}-{self.acnt_prdt_cd}",
            currency=self.currency,
            cash=float(max(cash, 0.0)),
            portfolio_value=float(max(portfolio, 0.0)),
            buying_power=float(max(buying_power, 0.0)),
            is_paper=self.is_paper,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = await self._headers("VTTC8434R" if self.is_paper else "TTTC8434R")
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

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params, attempts=4)
            if resp.status_code >= 400:
                return []

        positions: List[BrokerPosition] = []
        for row in data.get("output1", []):
            qty = float(row.get("hldg_qty", 0) or row.get("dnca_tot_qty", 0) or 0)
            if qty <= 0:
                continue
            symbol = str(row.get("pdno", "") or "")
            avg_price = float(row.get("pchs_avg_pric", 0) or 0)
            cur_price = float(row.get("prpr", 0) or 0)
            if cur_price <= 0 and symbol:
                try:
                    cur_price = await self._fetch_kr_price(symbol)
                except Exception:
                    cur_price = 0
            market_value = float(row.get("evlu_amt", qty * cur_price) or qty * cur_price)
            unrealized_pl = float(row.get("evlu_pfls_amt", 0) or 0)
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

    def _kr_tick_size(self, price: float) -> int:
        p = max(float(price), 0.0)
        if p < 2000:
            return 1
        if p < 5000:
            return 5
        if p < 20000:
            return 10
        if p < 50000:
            return 50
        if p < 200000:
            return 100
        if p < 500000:
            return 500
        return 1000

    def _normalize_kr_price(self, raw_price: float, side: str) -> int:
        tick = self._kr_tick_size(raw_price)
        p = int(max(raw_price, 1.0))
        if side.lower() == "sell":
            # round up for sell to avoid unnecessarily lower limit price
            return ((p + tick - 1) // tick) * tick
        # buy/default: round down to avoid accidental overpricing
        return (p // tick) * tick

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        is_buy = order.side.lower() == "buy"
        tr_id = (
            ("VTTC0802U" if is_buy else "VTTC0801U")
            if self.is_paper
            else ("TTTC0802U" if is_buy else "TTTC0801U")
        )
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        headers = await self._headers(tr_id)

        order_type = "01" if order.type.lower() == "limit" else "01"  # keep limit-compatible for safety
        price = order.limit_price or await self._fetch_kr_price(order.symbol)
        norm_price = self._normalize_kr_price(float(price), order.side)
        qty = int(order.qty)
        if qty <= 0:
            raise RuntimeError("KISKR invalid order qty: must be integer >= 1")
        payload = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": order.symbol,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(norm_price),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "POST", url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"KISKR order failed: status={resp.status_code}, body={data}")
            if str((data or {}).get("rt_cd", "")) not in {"", "0"}:
                raise RuntimeError(f"KISKR order rejected: {data}")

        out = data.get("output") or {}
        ord_no = str(out.get("ODNO", "") or out.get("odno", ""))
        return OrderResult(
            client_order_id=ord_no or f"kiskr-{datetime.now().timestamp()}",
            broker_order_id=ord_no,
            status="accepted",
            symbol=order.symbol,
            qty=order.qty,
        )

    async def cancel_order(self, order_id: str) -> bool:
        # Kept as best-effort placeholder for initial KRX test phase.
        return False

    async def get_market_status(self) -> bool:
        now_kst = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul"))
        if now_kst.weekday() >= 5:
            return False
        return dtime(9, 0) <= now_kst.time() <= dtime(15, 30)

    async def get_historicals(self, symbol: str, timeframe: str, limit: int) -> List[Any]:
        """
        KRX intraday/daily bars for strategy processing.

        Root-cause fix note:
        - Worker expects every broker adapter to implement get_historicals().
        - KISKRBroker previously lacked this method, causing per-symbol runtime errors.
        """
        tf = (timeframe or "").lower()
        safe_limit = max(int(limit or 1), 1)

        # Daily bars
        if tf in {"1d", "1day"}:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
            headers = await self._headers("FHKST03010100")
            today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
            # Request wider range then trim locally.
            start = (datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(days=max(safe_limit * 3, 120))).strftime("%Y%m%d")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": today,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
                if resp.status_code >= 400:
                    return []

            rows = (data or {}).get("output2") or []
            bars: List[SimpleBar] = []
            for row in rows:
                dt = str(row.get("stck_bsop_date", "") or "")
                if len(dt) != 8:
                    continue
                try:
                    ts = datetime.strptime(dt, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)
                except Exception:
                    continue

                o = float(row.get("stck_oprc", 0) or 0)
                h = float(row.get("stck_hgpr", 0) or 0)
                l = float(row.get("stck_lwpr", 0) or 0)
                c = float(row.get("stck_clpr", 0) or row.get("stck_prpr", 0) or 0)
                v = float(row.get("acml_vol", 0) or row.get("cntg_vol", 0) or 0)
                if c <= 0:
                    continue
                bars.append(SimpleBar(timestamp=ts, open=o or c, high=h or c, low=l or c, close=c, volume=v))

            bars.sort(key=lambda b: b.timestamp)
            return bars[-safe_limit:]

        minute_map = {
            "1min": 1,
            "1m": 1,
            "5min": 5,
            "5m": 5,
            "15min": 15,
            "15m": 15,
            "30min": 30,
            "30m": 30,
            "1hour": 60,
            "1h": 60,
        }
        bucket_min = minute_map.get(tf)
        if not bucket_min:
            return []

        # KIS KR intraday endpoint (1-minute feed). Aggregate to requested timeframe.
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers = await self._headers("FHKST03010200")
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": "",
            "FID_PW_DATA_INCU_YN": "Y",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
            if resp.status_code >= 400:
                return []

        rows = (data or {}).get("output2") or []
        one_min: List[SimpleBar] = []
        kst = ZoneInfo("Asia/Seoul")

        for row in rows:
            dt = str(row.get("stck_bsop_date", "") or "")
            tm = str(row.get("stck_cntg_hour", "") or "").zfill(6)
            if len(dt) != 8 or len(tm) != 6:
                continue
            try:
                ts = datetime.strptime(f"{dt}{tm}", "%Y%m%d%H%M%S").replace(tzinfo=kst).astimezone(timezone.utc)
            except Exception:
                continue

            o = float(row.get("stck_oprc", 0) or row.get("stck_prpr", 0) or 0)
            h = float(row.get("stck_hgpr", 0) or row.get("stck_prpr", 0) or 0)
            l = float(row.get("stck_lwpr", 0) or row.get("stck_prpr", 0) or 0)
            c = float(row.get("stck_prpr", 0) or 0)
            v = float(row.get("cntg_vol", 0) or 0)
            if c <= 0:
                continue
            one_min.append(SimpleBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v))

        if not one_min:
            return []

        one_min.sort(key=lambda b: b.timestamp)
        if bucket_min == 1:
            return one_min[-safe_limit:]

        # Aggregate to N-minute buckets.
        agg: List[SimpleBar] = []
        cur_start = None
        cur = None

        for b in one_min:
            ts_kst = b.timestamp.astimezone(kst)
            minute_floor = (ts_kst.minute // bucket_min) * bucket_min
            bucket_start_kst = ts_kst.replace(minute=minute_floor, second=0, microsecond=0)
            bucket_start = bucket_start_kst.astimezone(timezone.utc)

            if cur_start != bucket_start:
                if cur is not None:
                    agg.append(cur)
                cur_start = bucket_start
                cur = SimpleBar(
                    timestamp=bucket_start,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
            else:
                cur.high = max(cur.high, b.high)
                cur.low = min(cur.low, b.low)
                cur.close = b.close
                cur.volume += b.volume

        if cur is not None:
            agg.append(cur)

        return agg[-safe_limit:]

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> PortfolioHistory:
        account = await self.get_account_info()
        tf = timeframe.upper()
        period_key = period.upper()

        period_points = {"1D": 24, "5D": 40, "1W": 40, "1M": 30, "3M": 60, "6M": 120, "1A": 180, "1Y": 180}
        points = period_points.get(period_key, 30)
        step_map = {"1MIN": 60, "5MIN": 300, "15MIN": 900, "30MIN": 1800, "1H": 3600, "1D": 86400}
        step = step_map.get(tf, 86400)

        now = int(datetime.now(timezone.utc).timestamp())
        start = now - step * (points - 1)
        ts = [start + i * step for i in range(points)]

        equity = [float(account.portfolio_value or 0.0) for _ in range(points)]
        pnl = [0.0 for _ in range(points)]
        pnl_pct = [0.0 for _ in range(points)]
        return PortfolioHistory(timestamp=ts, equity=equity, profit_loss=pnl, profit_loss_pct=pnl_pct, timeframe=timeframe)

    async def get_trade_fills(self, limit: int = 100) -> List[TradeFill]:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        headers = await self._headers("VTTC8001R" if self.is_paper else "TTTC8001R")

        kst = ZoneInfo("Asia/Seoul")
        today = datetime.now(kst).date().strftime("%Y%m%d")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp, data = await self._request_with_retry(client, "GET", url, headers=headers, params=params)
            if resp.status_code >= 400:
                return []
            if str((data or {}).get("rt_cd", "0")) not in {"", "0"}:
                return []

        fills: List[TradeFill] = []
        rows = (data or {}).get("output1") or []
        for row in rows[:limit]:
            qty = float(row.get("tot_ccld_qty", 0) or row.get("ccld_qty", 0) or 0)
            if qty <= 0:
                continue
            symbol = str(row.get("pdno", "") or "")
            ord_no = str(row.get("odno", "") or "")
            ccld_no = str(row.get("ccld_no", "") or "")
            side = "buy" if str(row.get("sll_buy_dvsn_cd", "2")) in {"02", "2"} else "sell"
            price = float(row.get("avg_prvs", 0) or row.get("ord_unpr", 0) or 0)
            dt = str(row.get("ord_dt", today) or today)
            tm = str(row.get("ord_tmd", "000000") or "000000").zfill(6)
            try:
                executed_at = datetime.strptime(f"{dt}{tm}", "%Y%m%d%H%M%S").replace(tzinfo=kst).astimezone(timezone.utc)
            except Exception:
                executed_at = datetime.now(timezone.utc)
            fills.append(
                TradeFill(
                    execution_id=ccld_no or f"{ord_no}-{symbol}-{dt}{tm}",
                    order_id=ord_no or None,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                    commission=0.0,
                    executed_at=executed_at,
                )
            )
        return fills
