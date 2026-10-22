"""CSV-based DataProvider: bundled sample data and user-imported CSV files.

CSV format expected (header required):
    date,open,high,low,close,volume
`date` is parsed as a naive date/datetime and localized to Asia/Kolkata (NOT
converted — Indian OHLCV data is already in IST, so this attaches the
correct tzinfo rather than shifting the clock). Symbol is taken from the
filename stem, e.g. `SAMPLE_TCS.csv` -> symbol `TCS`.

This is the only provider implemented in Milestone 1. See
docs/DATA_SOURCES.md for why no network provider is implemented yet.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from app.data.base import DataProvider
from app.data.validation import validate_series
from app.models import Bar

IST = ZoneInfo("Asia/Kolkata")

REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}


class CsvProvider(DataProvider):
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _symbol_path(self, symbol: str) -> Path:
        candidates = list(self.directory.glob(f"*{symbol}*.csv"))
        exact = [p for p in candidates if p.stem.upper().endswith(symbol.upper())]
        chosen = exact or candidates
        if not chosen:
            raise FileNotFoundError(f"No CSV file found for symbol '{symbol}' in {self.directory}")
        if len(chosen) > 1:
            raise ValueError(
                f"Ambiguous CSV match for symbol '{symbol}' in {self.directory}: "
                f"{[p.name for p in chosen]}"
            )
        return chosen[0]

    def get_bars(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[Bar]:
        path = self._symbol_path(symbol)
        retrieved_at = datetime.now(tz=IST)
        source = f"csv:{path.name}"

        raw_bars: list[Bar] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = set(h.strip().lower() for h in (reader.fieldnames or []))
            missing = REQUIRED_COLUMNS - header
            if missing:
                raise ValueError(f"{path.name}: missing required columns {sorted(missing)}")

            for row_num, row in enumerate(reader, start=2):
                row = {k.strip().lower(): v for k, v in row.items()}
                try:
                    ts = _parse_date(row["date"]).replace(tzinfo=IST)
                    bar = Bar(
                        symbol=symbol.upper(),
                        timestamp=ts,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(float(row["volume"])),
                        source=source,
                        retrieved_at=retrieved_at,
                        interval=interval,
                    )
                except (KeyError, ValueError) as exc:
                    raise ValueError(f"{path.name}:{row_num}: malformed row ({exc})") from exc
                raw_bars.append(bar)

        raw_bars.sort(key=lambda b: b.timestamp)

        if start is not None:
            start_ts = _parse_date(start).replace(tzinfo=IST)
            raw_bars = [b for b in raw_bars if b.timestamp >= start_ts]
        if end is not None:
            end_ts = _parse_date(end).replace(tzinfo=IST)
            raw_bars = [b for b in raw_bars if b.timestamp <= end_ts]

        return validate_series(raw_bars)


def _parse_date(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: '{value}'")
