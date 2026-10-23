"""Command-line runner: rank swing candidates from local files.

PAPER TRADING -- NO REAL ORDERS. Reads files, prints a ranking, places nothing.

    python -m app.selection.run --bhavcopy-dir data/bhavcopy ^
        --index-dir data/index --index-symbol NIFTY50 [--as-of 2025-01-31]

The index series is NOT in the cash-market bhavcopy (NSE publishes indices
in a separate report), so you supply it as a CSV (date,open,high,low,close,
volume) read by CsvProvider. Without it the regime is UNCERTAIN and the
result is NO TRADE.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from app.data.bhavcopy_provider import BhavcopyProvider
from app.data.csv_provider import CsvProvider
from app.selection.ranking import SelectionResult, rank_swing_candidates


def format_result(res: SelectionResult) -> str:
    out = [
        "PAPER TRADING -- NO REAL ORDERS",
        f"As of {res.as_of}  |  regime: {res.regime.regime.value}",
        *(f"  - {r}" for r in res.regime.reasons),
        *(f"  (limitation) {s}" for s in res.regime.limitations),
        "",
    ]
    if res.no_trade:
        out.append(f"NO TRADE: {res.no_trade_reason}")
    for c in res.candidates:
        out += [
            f"#{c.rank} {c.symbol}  score {c.score:.1f}/100 (ranking aid, NOT a win probability)",
            f"   {c.setup} {c.bias}  entry {c.entry_zone[0]}-{c.entry_zone[1]}  "
            f"stop {c.stop_loss}  target {c.target}  R:R {c.reward_risk}",
            f"   data: {c.data_source} @ {c.data_as_of.date()}  events: {c.event_risk_status}",
            *(f"   + {r}" for r in c.qualified_because),
            *(f"   - {d}" for d in c.score_detractors),
            *(f"   ! {a}" for a in c.reasons_to_avoid),
        ]
    if res.rejections:
        out += ["", f"Rejected ({len(res.rejections)}):"]
        out += [f"   {r.symbol}: {r.rule} - {r.detail}" for r in res.rejections[:40]]
        if len(res.rejections) > 40:
            out.append(f"   ... {len(res.rejections) - 40} more")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rank swing candidates (paper only).")
    p.add_argument("--bhavcopy-dir", required=True)
    p.add_argument("--index-dir", required=True, help="folder with the index CSV")
    p.add_argument("--index-symbol", default="NIFTY50")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default = newest bhavcopy date")
    p.add_argument("--symbols", default=None, help="comma list; default = every EQ symbol")
    args = p.parse_args(argv)

    provider = BhavcopyProvider(args.bhavcopy_dir)
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols else provider.available_symbols()
    )
    universe = {s: provider.get_bars(s) for s in symbols}
    universe = {s: b for s, b in universe.items() if b}
    liquidity = {s: provider.get_liquidity(s) for s in universe}

    try:
        index_bars = CsvProvider(args.index_dir).get_bars(args.index_symbol)
    except FileNotFoundError as exc:
        print(f"Index data missing ({exc}); regime will be UNCERTAIN.", file=sys.stderr)
        index_bars = []

    if args.as_of:
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    else:
        newest = [b[-1].timestamp.date() for b in universe.values()]
        as_of = max(newest) if newest else date.today()

    print(format_result(rank_swing_candidates(universe, index_bars, as_of, liquidity)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
