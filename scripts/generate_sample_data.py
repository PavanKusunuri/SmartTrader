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
from datetime import date, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol, start_price, seed, drift in SERIES:
        rows = generate(symbol, start_price, seed, drift)
        path = OUT_DIR / f"{symbol}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {path} ({len(rows)} bars)")


if __name__ == "__main__":
    main()
