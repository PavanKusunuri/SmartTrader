"""CLI handlers for the read-only Groww data adapter.

PAPER TRADING — NO REAL ORDERS. These commands only read market data. Credentials
come from the environment / local .env (see app/groww_env.py); no value is ever
printed, and tokens are held in memory only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data.groww_client import GrowwDataError, GrowwReadOnlyClient
from app.data.groww_provider import INTERVALS, SOURCE_TZ_NOTE, GrowwProvider
from app.groww_env import load_groww_credentials
from app.models import DataQuality

IST = ZoneInfo("Asia/Kolkata")
BANNER = "PAPER TRADING — NO REAL ORDERS (Groww access here is READ-ONLY market data)"


def build_provider(env_file: str) -> GrowwProvider:
    """Authenticates from the local environment. Raises GrowwDataError with a
    credential-free message on any failure."""
    creds = load_groww_credentials(env_file)
    mode = creds.auth_mode()
    if mode is None:
        raise GrowwDataError(
            "no complete Groww credential set found. Run `check-env`; see .env.example. "
            "Never paste credentials into chat."
        )
    secret = lambda s: s.get_secret_value() if s is not None else None  # noqa: E731
    client = GrowwReadOnlyClient()
    client.authenticate(
        access_token=secret(creds.access_token),
        api_key=secret(creds.api_key), api_secret=secret(creds.api_secret),
        totp_token=secret(creds.totp_token), totp_secret=secret(creds.totp_secret),
    )
    return GrowwProvider(client)


def cmd_groww_probe(args: argparse.Namespace) -> int:
    """Measures what Groww actually serves, because its docs contradict themselves
    on intraday depth. Read-only; prints counts and timestamps, never secrets."""
    print(BANNER)
    try:
        provider = build_provider(args.env_file)
    except GrowwDataError as exc:
        print(f"\nCannot authenticate: {exc}")
        return 2

    symbol = args.symbol.strip().upper()
    now = datetime.now(tz=IST)
    print(f"\nSymbol {symbol}, probing {args.interval} candles (one 2-day window per age):")
    print(f"{'age (days)':>12}  {'candles':>8}  first / last timestamp")
    deepest = None
    for age in (3, 30, 60, 100, 200, 400, 800):
        end = now - timedelta(days=age)
        try:
            bars = provider.get_bars(
                symbol, args.interval, start=(end - timedelta(days=4)).strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except GrowwDataError as exc:
            print(f"{age:>12}  {'error':>8}  {exc}")
            continue
        if bars:
            deepest = age
            print(f"{age:>12}  {len(bars):>8}  {bars[0].timestamp:%Y-%m-%d %H:%M} / {bars[-1].timestamp:%Y-%m-%d %H:%M}")
        else:
            print(f"{age:>12}  {0:>8}  (none; may be a holiday window or beyond available depth)")

    print(f"\nDeepest age that returned data: {deepest if deepest is not None else 'none'} days")
    print(
        f"CHECK BY HAND: compare one printed candle with Groww's own chart for {symbol}. "
        f"Timestamps are ASSUMED IST / candle-open ({SOURCE_TZ_NOTE}); Groww's docs do not say."
    )
    return 0 if deepest is not None else 1


def cmd_groww_fetch(args: argparse.Namespace) -> int:
    """Downloads candles into the CSV format the rest of the app already reads.

    Output: <out>/<interval>/<SYMBOL>.csv (merged with any existing file so a daily
    fetch accumulates history) plus <out>/<interval>/_manifest.json recording the
    source, retrieval time, interval and data-quality warnings (CLAUDE.md §6).
    Use it with:  scan --data-dir <out>/<interval> --interval <interval> ...
    """
    print(BANNER)
    if args.interval not in INTERVALS:
        print(f"Unsupported interval {args.interval!r}; use one of {', '.join(sorted(INTERVALS))}")
        return 2
    try:
        provider = build_provider(args.env_file)
    except GrowwDataError as exc:
        print(f"\nCannot authenticate: {exc}")
        return 2

    out_dir = Path(args.out) / args.interval
    out_dir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(tz=IST)
    start = end - timedelta(days=args.days)
    manifest: dict = {
        "source": "groww:candles", "kind": "REAL_HISTORICAL_FROM_BROKER_API",
        "interval": args.interval, "retrieved_at": end.isoformat(),
        "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "timestamp_assumption": SOURCE_TZ_NOTE, "streaming": False, "symbols": {},
    }
    failures = 0
    for raw in args.symbols.split(","):
        symbol = raw.strip().upper()
        if not symbol:
            continue
        try:
            bars = provider.get_bars(symbol, args.interval, start=start.strftime("%Y-%m-%d"),
                                     end=end.strftime("%Y-%m-%d %H:%M:%S"))
        except (GrowwDataError, ValueError) as exc:
            failures += 1
            manifest["symbols"][symbol] = {"error": str(exc)}
            print(f"  {symbol:<14}FAILED  {exc}")
            continue
        if not bars:
            manifest["symbols"][symbol] = {"rows": 0, "warning": "no candles returned"}
            print(f"  {symbol:<14}no candles returned")
            continue
        added = _merge_csv(out_dir / f"{symbol}.csv", bars)
        flagged = [b for b in bars if b.quality is not DataQuality.OK]
        manifest["symbols"][symbol] = {
            "rows_fetched": len(bars), "rows_in_file": added,
            "first": bars[0].timestamp.isoformat(), "last": bars[-1].timestamp.isoformat(),
            "quality_flagged": len(flagged),
            "quality_notes": sorted({n for b in flagged for n in b.quality_notes})[:10],
        }
        print(f"  {symbol:<14}{len(bars):>6} candles fetched, {added} in file, {len(flagged)} flagged")

    _atomic_write(out_dir / "_manifest.json", json.dumps(manifest, indent=2))
    print(f"\nWritten to {out_dir} (manifest: _manifest.json)")
    return 1 if failures else 0


def _merge_csv(path: Path, bars) -> int:
    rows: dict[str, list[str]] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = [r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]]
    for b in bars:
        key = b.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        rows[key] = [key, repr(b.open), repr(b.high), repr(b.low), repr(b.close), str(b.volume)]
    body = ["date,open,high,low,close,volume"] + [",".join(rows[k]) for k in sorted(rows)]
    _atomic_write(path, "\n".join(body) + "\n")
    return len(rows)


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename so a crash never leaves a half-written file behind."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
