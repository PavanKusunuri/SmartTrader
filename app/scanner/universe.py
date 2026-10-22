"""The eligible trading universe (watchlist).

Deliberately small by default. CLAUDE.md section 10 requires starting with a
configurable watchlist rather than scanning every NSE symbol: a broad scan
multiplies provider load, rate-limit risk, and the chance of acting on a
symbol whose data nobody has checked.

A symbol that is not in the watchlist is not tradable, and the scanner
returns NO_TRADE for it rather than silently ignoring the request.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# Hard ceiling on watchlist size for v1. This is a guard against someone
# pointing the scanner at a few thousand symbols and hammering a provider,
# not a claim about what is a sensible number to trade.
MAX_WATCHLIST_SIZE = 50


class InvalidWatchlist(ValueError):
    pass


@dataclass(frozen=True)
class Watchlist:
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.symbols:
            raise InvalidWatchlist("watchlist is empty; nothing to scan")
        if len(self.symbols) > MAX_WATCHLIST_SIZE:
            raise InvalidWatchlist(
                f"watchlist has {len(self.symbols)} symbols, above the v1 cap of "
                f"{MAX_WATCHLIST_SIZE}. Narrow it rather than raising the cap."
            )
        duplicates = {s for s in self.symbols if self.symbols.count(s) > 1}
        if duplicates:
            raise InvalidWatchlist(f"duplicate symbols in watchlist: {sorted(duplicates)}")
        for symbol in self.symbols:
            if not symbol or symbol != symbol.strip():
                raise InvalidWatchlist(f"symbol {symbol!r} has leading/trailing whitespace")
            if symbol != symbol.upper():
                raise InvalidWatchlist(f"symbol {symbol!r} must be upper-case")

    def __contains__(self, symbol: object) -> bool:
        return isinstance(symbol, str) and symbol.upper() in self.symbols

    def __iter__(self):
        return iter(self.symbols)

    def __len__(self) -> int:
        return len(self.symbols)

    @classmethod
    def from_list(cls, symbols: list[str]) -> Watchlist:
        cleaned = tuple(s.strip().upper() for s in symbols if s and s.strip())
        return cls(symbols=cleaned)

    @classmethod
    def from_csv(cls, path: str | Path) -> Watchlist:
        """Loads a watchlist from a CSV with a `symbol` column. Raises rather
        than returning an empty watchlist, so a typo'd path cannot silently
        turn into "scan nothing"."""

        path = Path(path)
        if not path.exists():
            raise InvalidWatchlist(f"watchlist file not found: {path}")

        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = [h.strip().lower() for h in (reader.fieldnames or [])]
            if "symbol" not in headers:
                raise InvalidWatchlist(f"{path.name}: missing required 'symbol' column")
            symbols = []
            for row in reader:
                row = {k.strip().lower(): v for k, v in row.items()}
                value = (row.get("symbol") or "").strip()
                if value:
                    symbols.append(value)

        if not symbols:
            raise InvalidWatchlist(f"{path.name}: no symbols found")
        return cls.from_list(symbols)
