from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta, time, timezone
from typing import List, Tuple, Optional
import logging
import requests
import asyncio

from app.brokers.factory import create_broker

from sqlalchemy import select, and_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.core.config import settings
from app.domain.models import Candle, LogEntry, DataDownloadRecord

logger = logging.getLogger(__name__)


class DataService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # Timeframe mapping (normalized lowercase -> Alpaca API format)
    TIMEFRAME_MAP = {
        "1min": "1Min",
        "5min": "5Min",
        "15min": "15Min",
        "30min": "30Min",
        "1hour": "1Hour",
        "1day": "1Day",
        # Legacy formats
        "1m": "1Min",
        "5m": "5Min",
        "15m": "15Min",
        "30m": "30Min",
        "1h": "1Hour",
        "1d": "1Day",
    }

    def _normalize_timeframe(self, timeframe: str) -> str:
        """Normalize timeframe to Alpaca API format"""
        tf_lower = timeframe.lower()
        api_tf = self.TIMEFRAME_MAP.get(tf_lower)
        if not api_tf:
            raise ValueError(
                f"Unsupported timeframe: {timeframe}. Supported: {list(self.TIMEFRAME_MAP.keys())}"
            )
        return api_tf

    async def _get_existing_records(
        self, symbol: str, timeframe: str
    ) -> List[DataDownloadRecord]:
        """Get all download records for a symbol/timeframe combination"""
        stmt = (
            select(DataDownloadRecord)
            .where(
                and_(
                    DataDownloadRecord.symbol == symbol,
                    DataDownloadRecord.timeframe == timeframe,
                )
            )
            .order_by(DataDownloadRecord.start_date.asc())
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    def _intervals_overlap_or_adjacent(
        self, s1: date, e1: date, s2: date, e2: date
    ) -> bool:
        """Check if two intervals overlap or are adjacent (within 1 day)"""
        # Adjacent: e1 + 1 day >= s2 or e2 + 1 day >= s1
        return not (e1 + timedelta(days=1) < s2 or e2 + timedelta(days=1) < s1)

    async def _update_download_records(
        self, symbol: str, timeframe: str, new_start: date, new_end: date
    ):
        """
        Update download records with interval merging logic.
        If new range overlaps/covers existing ranges, merge them into one.
        If separate, keep as separate records.
        """
        existing_records = await self._get_existing_records(symbol, timeframe)

        if not existing_records:
            # No existing records, create new one
            record = DataDownloadRecord(
                symbol=symbol,
                timeframe=timeframe,
                start_date=new_start,
                end_date=new_end,
            )
            self.db.add(record)
            return

        # Find all records that overlap with new range
        overlapping_ids = []
        merged_start = new_start
        merged_end = new_end

        for rec in existing_records:
            # Convert datetime to date if needed for comparison
            rec_start = (
                rec.start_date.date()
                if hasattr(rec.start_date, "date")
                else rec.start_date
            )
            rec_end = (
                rec.end_date.date() if hasattr(rec.end_date, "date") else rec.end_date
            )

            if self._intervals_overlap_or_adjacent(
                rec_start, rec_end, new_start, new_end
            ):
                overlapping_ids.append(rec.id)
                merged_start = min(merged_start, rec_start)
                merged_end = max(merged_end, rec_end)

        if overlapping_ids:
            # Delete overlapping records
            await self.db.execute(
                delete(DataDownloadRecord).where(
                    DataDownloadRecord.id.in_(overlapping_ids)
                )
            )
            # Create merged record
            merged_record = DataDownloadRecord(
                symbol=symbol,
                timeframe=timeframe,
                start_date=merged_start,
                end_date=merged_end,
            )
            self.db.add(merged_record)
        else:
            # No overlap, add as new separate record
            record = DataDownloadRecord(
                symbol=symbol,
                timeframe=timeframe,
                start_date=new_start,
                end_date=new_end,
            )
            self.db.add(record)

    async def check_download_needed(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        timeframe: str,  # Required, no default
    ) -> bool:
        """
        Check if download is needed based on download records.
        Returns True if ANY part of the requested range is not covered.
        """
        existing_records = await self._get_existing_records(symbol, timeframe)

        if not existing_records:
            return True  # No records, need download

        # Check if any record fully covers the requested range
        for rec in existing_records:
            # Convert datetime to date if needed for comparison
            rec_start = (
                rec.start_date.date()
                if hasattr(rec.start_date, "date")
                else rec.start_date
            )
            rec_end = (
                rec.end_date.date() if hasattr(rec.end_date, "date") else rec.end_date
            )

            if rec_start <= start_date and rec_end >= end_date:
                return False  # Fully covered

        return True  # Not fully covered

    async def download_historical(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str,  # Required, no default!
    ):
        """
        Download historical data from configured broker and save to DB.
        Updates download records with interval merging.
        """
        saved_count = 0

        if settings.BROKER_MODE.lower() == "kis":
            broker = create_broker()

            # KIS supports 1m/5m/15m/30m/1h/1d in broker adapter.
            tf_norm = timeframe.lower()
            supported = {
                "1d",
                "1day",
                "1min",
                "1m",
                "5min",
                "5m",
                "15min",
                "15m",
                "30min",
                "30m",
                "1hour",
                "1h",
            }
            if tf_norm not in supported:
                logger.warning(f"KIS mode unsupported timeframe={timeframe}")
                return 0

            fetch_limit = 120 if tf_norm not in {"1d", "1day"} else 1000

            added_keys = set()
            for sym in symbols:
                try:
                    bars = await broker.get_historicals(sym, timeframe, fetch_limit)
                except Exception as e:
                    logger.error(f"KIS historical fetch failed for {sym}: {e}")
                    continue

                for bar in bars:
                    ts = bar.timestamp
                    bar_date = ts.date()
                    if bar_date < start_date or bar_date > end_date:
                        continue

                    candle_key = (sym, timeframe, ts)
                    if candle_key in added_keys:
                        continue

                    stmt = (
                        pg_insert(Candle)
                        .values(
                            symbol=sym,
                            timeframe=timeframe,
                            timestamp=ts,
                            open=float(bar.open),
                            high=float(bar.high),
                            low=float(bar.low),
                            close=float(bar.close),
                            volume=float(bar.volume),
                        )
                        .on_conflict_do_nothing(
                            index_elements=["symbol", "timeframe", "timestamp"]
                        )
                    )
                    await self.db.execute(stmt)
                    added_keys.add(candle_key)
                    saved_count += 1

            await self.db.commit()

            for sym in symbols:
                await self._update_download_records(
                    sym, timeframe, start_date, end_date
                )
            await self.db.commit()

            log = LogEntry(
                level="INFO",
                source="DataService",
                message=f"Downloaded {saved_count} candles for {len(symbols)} symbols ({timeframe})",
                context={
                    "symbols": symbols,
                    "timeframe": timeframe,
                    "start": str(start_date),
                    "end": str(end_date),
                    "broker": "KIS",
                },
            )
            self.db.add(log)
            await self.db.commit()
            logger.info(
                f"Downloaded {saved_count} candles for {symbols} ({timeframe}) from {start_date} to {end_date}"
            )
            return saved_count

        # Default: Alpaca flow
        api_tf = self._normalize_timeframe(timeframe)

        url = "https://data.alpaca.markets/v2/stocks/bars"
        headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
            "accept": "application/json",
        }

        loop = asyncio.get_event_loop()

        # Track candles added in this session to avoid duplicates
        added_keys = set()  # (symbol, timeframe, timestamp)

        # Split symbols into chunks of 20
        chunk_size = 20
        for i in range(0, len(symbols), chunk_size):
            chunk_syms = symbols[i : i + chunk_size]
            syms_str = ",".join(chunk_syms)

            # Legacy parity:
            # - do not force feed (old data server did not hardcode feed)
            # - use datetime boundaries (not date-only) to avoid truncating intraday tail bars
            # - cap end to now-16min (same safety used in old)
            start_dt = datetime.combine(start_date, time.min).replace(
                tzinfo=timezone.utc
            )
            requested_end_dt = datetime.combine(end_date, time.max).replace(
                tzinfo=timezone.utc
            )
            safe_end_dt = datetime.now(timezone.utc) - timedelta(minutes=16)
            end_dt = min(requested_end_dt, safe_end_dt)

            params = {
                "symbols": syms_str,
                "timeframe": api_tf,
                "start": start_dt.isoformat().replace("+00:00", "Z"),
                "end": end_dt.isoformat().replace("+00:00", "Z"),
                "limit": 10000,
                "adjustment": "raw",
            }

            while True:
                response = await loop.run_in_executor(
                    None,
                    lambda p=dict(params): requests.get(url, headers=headers, params=p),
                )

                if response.status_code != 200:
                    logger.error(
                        f"Alpaca API error: {response.status_code} - {response.text}"
                    )
                    break

                data = response.json()
                bars_map = data.get("bars", {})

                for sym, bars in bars_map.items():
                    for bar in bars:
                        ts_str = bar["t"]
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

                        # Create key for deduplication in this session
                        candle_key = (sym, timeframe, ts)
                        if candle_key in added_keys:
                            continue

                        # Use PostgreSQL INSERT ON CONFLICT DO NOTHING
                        stmt = (
                            pg_insert(Candle)
                            .values(
                                symbol=sym,
                                timeframe=timeframe,
                                timestamp=ts,
                                open=float(bar["o"]),
                                high=float(bar["h"]),
                                low=float(bar["l"]),
                                close=float(bar["c"]),
                                volume=float(bar["v"]),
                            )
                            .on_conflict_do_nothing(
                                index_elements=["symbol", "timeframe", "timestamp"]
                            )
                        )
                        await self.db.execute(stmt)
                        added_keys.add(candle_key)
                        saved_count += 1

                next_token = data.get("next_page_token")
                if not next_token:
                    break
                params["page_token"] = next_token

        await self.db.commit()

        # Update download records for each symbol
        for sym in symbols:
            await self._update_download_records(sym, timeframe, start_date, end_date)

        await self.db.commit()

        log = LogEntry(
            level="INFO",
            source="DataService",
            message=f"Downloaded {saved_count} candles for {len(symbols)} symbols ({timeframe})",
            context={
                "symbols": symbols,
                "timeframe": timeframe,
                "start": str(start_date),
                "end": str(end_date),
            },
        )
        self.db.add(log)
        await self.db.commit()

        logger.info(
            f"Downloaded {saved_count} candles for {symbols} ({timeframe}) from {start_date} to {end_date}"
        )
        return saved_count

    async def check_data_availability(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str,  # Required, no default!
    ) -> List[str]:
        """
        Check which symbols need data download for the given range.
        Uses download records for efficient checking.
        """
        missing_symbols = []

        for sym in symbols:
            if await self.check_download_needed(sym, start_date, end_date, timeframe):
                missing_symbols.append(sym)

        return missing_symbols
