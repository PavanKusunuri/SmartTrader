"""Generates synthetic, clearly-labelled sample OHLCV CSVs for local testing.

This data is NOT real historical market data. It is a deterministic random
walk used only so Milestone 1 has something to run against end-to-end
without a network call or a paid API. Do not use it to draw conclusions
about any real stock's behaviour.

Run: python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

FIELDS = ["date", "open", "high", "low", "close", "volume"]

# Intraday sample settings: 5-minute bars across a regular 09:15-15:30 session.
INTRADAY_SERIES = [("SAMPLE_INTRADAY_TCS", 3500.0, 99), ("SAMPLE_INTRADAY_INFY", 1500.0, 123)]
INTRADAY_DAYS = 20
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
BAR_MINUTES = 5

# (filename symbol, start price, seed, daily drift) — drift/seed chosen only
# to produce a visible-but-fake uptrend/downtrend for testing, not a forecast.
SERIES = [
    ("SAMPLE_TCS", 3500.0, 42, 0.0006),
    ("SAMPLE_INFY", 1500.0, 7, -0.0003),
]

NUM_DAYS = 180


def generate(symbol: str, start_price: float, seed: int, drift: float) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    price = start_price
    d = date(2024, 1, 1)
    count = 0
    while count < NUM_DAYS:
        d += timedelta(days=1)
        if d.weekday() >= 5:  # skip weekends; no holiday calendar in sample data
            continue
        daily_return = drift + rng.gauss(0, 0.012)
        open_price = price
        close_price = max(0.5, open_price * (1 + daily_return))
        high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, 0.004)))
        low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, 0.004)))
        volume = rng.randint(200_000, 2_000_000)
        rows.append(
            {
                "date": d.isoformat(),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": volume,
            }
        )
        price = close_price
        count += 1
    return rows


def generate_intraday(symbol: str, start_price: float, seed: int) -> list[dict]:
    """5-minute bars across INTRADAY_DAYS weekday sessions.

    Synthetic, like the daily data. Volume is given an intraday U-shape
    (busier at the open and close) only so the volume filter has something
    non-constant to react to — it is not calibrated to any real stock.
    """

    rng = random.Random(seed)
    rows: list[dict] = []
    price = start_price
    d = date(2024, 1, 1)
    sessions = 0

    bars_per_session = (
        (SESSION_END.hour * 60 + SESSION_END.minute)
        - (SESSION_START.hour * 60 + SESSION_START.minute)
    ) // BAR_MINUTES

    while sessions < INTRADAY_DAYS:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        ts = datetime.combine(d, SESSION_START)
        for i in range(bars_per_session):
            bar_return = rng.gauss(0, 0.0015)
            open_price = price
            close_price = max(0.5, open_price * (1 + bar_return))
            high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, 0.0008)))
            low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, 0.0008)))

            # U-shaped volume profile across the session.
            position_in_day = i / max(1, bars_per_session - 1)
            u_shape = 1.0 + 1.5 * ((position_in_day - 0.5) ** 2) * 4
            volume = int(rng.randint(20_000, 60_000) * u_shape)

            rows.append(
                {
                    "date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": round(open_price, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "close": round(close_price, 2),
                    "volume": volume,
                }
            )
            price = close_price
            ts += timedelta(minutes=BAR_MINUTES)
        sessions += 1
    return rows


def write_csv(symbol: str, rows: list[dict]) -> None:
    path = OUT_DIR / f"{symbol}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path} ({len(rows)} bars)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol, start_price, seed, drift in SERIES:
        write_csv(symbol, generate(symbol, start_price, seed, drift))
    for symbol, start_price, seed in INTRADAY_SERIES:
        write_csv(symbol, generate_intraday(symbol, start_price, seed))


if __name__ == "__main__":
    main()
