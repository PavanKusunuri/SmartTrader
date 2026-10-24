"""Groww-backed DataProvider (read-only historical candles + quote snapshot).

PAPER TRADING — NO REAL ORDERS. Needs an active Groww Trading API subscription
and credentials in the local, git-ignored .env (see app/groww_env.py). Nothing
here can place an order (see app/data/groww_client.py).

What this is and is not:
  * Historical candles are REAL exchange-derived history as served by Groww; they
    are not "sample" data. Delivery is by polling REST, never streaming.
  * ``get_latest_quote`` is a REST snapshot at call time, not a stream, and it is
    labelled as such in ``source``. Do not call it real-time or tick-by-tick.
  * UNVERIFIED: Groww's docs do not state the timezone of candle timestamps, nor
    whether the stamp is a candle's open or close time. Naive timestamps are
    ASSUMED to be IST and treated as candle OPEN time; ``source`` carries that
    assumption. Run ``groww-probe`` and compare one candle with Groww's own chart
    before trusting any result built on this data.
  * Intraday history depth is contradictory in Groww's docs (3 months vs. from
    2020); ``groww-probe`` measures it rather than assuming.
  * Prices are as served (corporate-action adjustment is not documented).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.data.base import DataProvider
from app.data.groww_client import GrowwDataError, GrowwReadOnlyClient
from app.data.validation import validate_series
from app.models import Bar

IST = ZoneInfo("Asia/Kolkata")

# App interval label -> (Groww candle_interval, max days per request).
# Max days are Groww's documented "backtesting" limits (30 / 90 / 180); one day
# is shaved off each so a window never sits exactly on the boundary.
INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("1minute", 29), "2m": ("2minute", 29), "3m": ("3minute", 29), "5m": ("5minute", 29),
    "10m": ("10minute", 89), "15m": ("15minute", 89), "30m": ("30minute", 89),
    "1h": ("1hour", 179), "4h": ("4hour", 179), "1d": ("1day", 179), "1w": ("1week", 179),
}

_SYMBOL = re.compile(r"^[A-Z0-9&_-]{1,20}$")
_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

SOURCE_TZ_NOTE = "tz-assumed-IST,stamp-assumed-open"


class GrowwProvider(DataProvider):
    def __init__(self, client: GrowwReadOnlyClient, *, exchange: str = "NSE", segment: str = "CASH"):
        if exchange not in ("NSE", "BSE") or segment != "CASH":
            raise ValueError("only CASH-segment NSE/BSE equities are supported")
        self._client = client
        self._exchange = exchange
        self._segment = segment

    # ------------------------------------------------------------------ bars

    def get_bars(
        self, symbol: str, interval: str = "1d",
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[Bar]:
        sym = _clean_symbol(symbol)
        if interval not in INTERVALS:
            raise ValueError(f"unsupported interval {interval!r}; use one of {sorted(INTERVALS)}")
        candle_interval, max_days = INTERVALS[interval]

        now = datetime.now(tz=IST)
        end_dt = min(_parse(end) if end else now, now)
        start_dt = _parse(start) if start else end_dt - timedelta(days=max_days)
        if start_dt >= end_dt:
            raise ValueError("start must be before end")

        retrieved_at = now
        source = f"groww:candles:{candle_interval}({SOURCE_TZ_NOTE})"
        by_ts: dict[datetime, Bar] = {}

        window_start = start_dt
        while window_start < end_dt:
            window_end = min(window_start + timedelta(days=max_days), end_dt)
            payload = self._client.historical_candles(
                exchange=self._exchange, segment=self._segment,
                groww_symbol=f"{self._exchange}-{sym}",
                start_time=window_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=window_end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval=candle_interval,
            )
            for row in payload.get("candles") or []:
                bar = _row_to_bar(row, sym, interval, source, retrieved_at)
                by_ts[bar.timestamp] = bar   # overlap between windows collapses here
            window_start = window_end

        ordered = [by_ts[t] for t in sorted(by_ts)]
        return validate_series(ordered)

    # ----------------------------------------------------------------- quote

    def get_latest_quote(self, symbol: str) -> Optional[Bar]:
        """A point-in-time REST snapshot, NOT a stream. Raises on failure: a
        data error must never be turned into a price."""
        sym = _clean_symbol(symbol)
        q = self._client.quote(exchange=self._exchange, segment=self._segment, trading_symbol=sym)
        try:
            ohlc = q["ohlc"]
            last = float(q["last_price"])
            ts_ms = q.get("last_trade_time")
            stamp = (datetime.fromtimestamp(ts_ms / 1000, tz=IST) if ts_ms else None)
            if stamp is None:
                raise GrowwDataError("quote had no last_trade_time")
            bar = Bar(
                symbol=sym, timestamp=stamp,
                open=float(ohlc["open"]), high=float(ohlc["high"]),
                low=float(ohlc["low"]), close=last,
                volume=int(q.get("volume") or 0),
                source="groww:quote:rest-snapshot(not-streaming)",
                retrieved_at=datetime.now(tz=IST), interval="quote",
                quality_notes=("open/high/low are DAY values; close is last traded price",),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GrowwDataError(f"quote for {sym} had an unexpected shape ({type(exc).__name__})") from exc
        return validate_series([bar])[0]


# --------------------------------------------------------------------- helpers


def _clean_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not _SYMBOL.match(sym):
        raise ValueError(f"invalid symbol {symbol!r}")
    return sym


def _parse(value: str) -> datetime:
    text = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    raise ValueError(f"unrecognized date/time {value!r}")


def _row_to_bar(row: list, symbol: str, interval: str, source: str, retrieved_at: datetime) -> Bar:
    try:
        raw_ts, o, h, l, c, v = row[:6]
        if isinstance(raw_ts, (int, float)):
            seconds = raw_ts / 1000 if raw_ts > 1e11 else raw_ts   # epoch s (or ms)
            ts = datetime.fromtimestamp(seconds, tz=IST)
        else:
            ts = _parse(str(raw_ts))
        return Bar(
            symbol=symbol, timestamp=ts, open=float(o), high=float(h), low=float(l),
            close=float(c), volume=int(float(v or 0)), source=source,
            retrieved_at=retrieved_at, interval=interval,
        )
    except (ValueError, TypeError) as exc:
        raise GrowwDataError(f"malformed candle row for {symbol} ({type(exc).__name__})") from exc
