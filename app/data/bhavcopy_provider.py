"""NSE UDiFF bhavcopy DataProvider: real NSE daily OHLCV from files you download.

HOW YOU GET THE DATA (manual, by design)
----------------------------------------
You download the daily "CM-UDiFF Common Bhavcopy Final" file yourself from
nseindia.com (All Reports) in a browser, and drop it in one directory. This
app never fetches it, and deliberately contains no code that could.
CLAUDE.md section 6 forbids scraping NSE or bypassing its access controls,
and NSE's site actively blocks automation; a file you downloaded by hand is
your own lawful copy, not an automated scrape. See docs/DATA_SOURCES.md.

Accepts either the raw `.csv` or the `.csv.zip` exactly as NSE serves it.

FORMAT -- VERIFIED, NOT GUESSED
-------------------------------
The column names below were read from a real 2025-12-30 UDiFF file, not
from a blog post. NSE replaced the legacy bhavcopy with UDiFF effective
2024-07-08 (circular 62424 of 2024-06-12), which renamed every column:
`SYMBOL` became `TckrSymb`, `TOTTRDQTY` became `TtlTradgVol`, and so on.

This provider reads UDiFF ONLY. A legacy-format file is rejected with a
clear error rather than parsed on a guess at what its columns mean. The
practical consequence: history starts 2024-07-08. For anything earlier you
need a different source.

WHAT THIS DATA IS AND IS NOT
----------------------------
- Real exchange-published end-of-day data. Not synthetic, not delayed-live.
- UNADJUSTED for corporate actions. A split, bonus, or demerger appears as
  a cliff in the price series. Nothing here detects or adjusts for that, so
  a backtest spanning a split WILL read the gap as a crash.
  `validate_series` flags such a bar SUSPECT_GAP, which is a symptom, not a
  fix.
- End-of-day only. There is no intraday bar and no quote here; a strategy
  needing either must use a different provider.
- No delivery percentage: that lives in NSE's separate `sec_bhavdata_full`
  report, which is not this file.
"""

from __future__ import annotations

import csv
import io
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from app.data.base import DataProvider
from app.data.validation import validate_series
from app.models import Bar

IST = ZoneInfo("Asia/Kolkata")

# Verified against a real 2025-12-30 CM-UDiFF file. Only the columns this
# provider actually reads are named; UDiFF carries ~34 including derivatives
# fields (StrkPric, OptnTp, OpnIntrst) that cash equity ignores.
COL_DATE = "TradDt"
COL_SYMBOL = "TckrSymb"
COL_SERIES = "SctySrs"
COL_INSTRUMENT = "FinInstrmTp"
COL_OPEN = "OpnPric"
COL_HIGH = "HghPric"
COL_LOW = "LwPric"
COL_CLOSE = "ClsPric"
COL_VOLUME = "TtlTradgVol"
COL_TURNOVER = "TtlTrfVal"
COL_TRADES = "TtlNbOfTxsExctd"

REQUIRED_COLUMNS = frozenset(
    {
        COL_DATE, COL_SYMBOL, COL_SERIES, COL_INSTRUMENT,
        COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOLUME,
    }
)

# Columns unique to the pre-2024-07-08 legacy bhavcopy. Their presence means
# the caller handed us a file this parser must not try to interpret.
LEGACY_MARKER_COLUMNS = frozenset({"SYMBOL", "TOTTRDQTY", "TOTTRDVAL", "PREVCLOSE"})

# `STK` is the cash-equity instrument type. The same file also carries gold
# bonds, ETFs and other instruments, which cash-equity strategies must not
# silently treat as shares.
EQUITY_INSTRUMENT_TYPE = "STK"

# `EQ` is the normal rolling-settlement series. `BE` is trade-for-trade:
# real equity, but with no intraday netting, typically thinner and more
# surveillance-constrained, so it is excluded by default rather than mixed in.
DEFAULT_SERIES = frozenset({"EQ"})


class BhavcopyFormatError(ValueError):
    """The file is not a readable UDiFF bhavcopy."""


@dataclass(frozen=True)
class LiquidityRecord:
    """Per-day traded value and trade count for one symbol.

    Kept separate from `Bar` deliberately: these are tradability-filter
    inputs that only this provider supplies. A strategy that silently
    depended on them would break on any CSV source that cannot.
    """

    symbol: str
    date: datetime
    turnover: float       # TtlTrfVal -- rupee traded value for the day
    trade_count: int      # TtlNbOfTxsExctd -- number of executed trades
    volume: int
    close: float


@contextmanager
def _open_text(path: Path) -> Iterator[io.TextIOBase]:
    """Yields a text stream for a `.csv` or a `.csv.zip`, so the user can
    drop NSE's zip in unchanged instead of unpacking it first."""

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise BhavcopyFormatError(f"{path.name}: zip contains no .csv file")
            if len(names) > 1:
                raise BhavcopyFormatError(
                    f"{path.name}: zip holds {len(names)} csv files, expected 1: {names}"
                )
            with zf.open(names[0]) as raw:
                # UDiFF is specified as ASCII with CRLF line endings.
                yield io.TextIOWrapper(raw, encoding="ascii", errors="replace", newline="")
    else:
        with path.open(newline="", encoding="ascii", errors="replace") as fh:
            yield fh


def _parse_trade_date(value: str) -> datetime:
    """UDiFF `TradDt` is ISO `YYYY-MM-DD`.

    Stamped at MIDNIGHT IST of the trading date, matching `CsvProvider` so
    daily bars from either source sort and compare identically. That is a
    date label, not a claim the bar printed at 00:00.
    """

    value = (value or "").strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=IST)
    except ValueError as exc:
        raise BhavcopyFormatError(f"unrecognized {COL_DATE} value '{value}'") from exc


def _to_float(value: str, column: str, context: str) -> float:
    text = (value or "").strip().replace(",", "")
    if not text:
        raise BhavcopyFormatError(f"{context}: empty {column}")
    try:
        return float(text)
    except ValueError as exc:
        raise BhavcopyFormatError(f"{context}: non-numeric {column} '{value}'") from exc


class BhavcopyProvider(DataProvider):
    """Reads a directory of per-date UDiFF files as per-symbol time series.

    A bhavcopy is cross-sectional (one date, every symbol) while
    `DataProvider` is per-symbol (one symbol, every date), so this class
    pivots. Every file in the directory is parsed once on first use and
    cached in memory; call `refresh()` after adding new files.
    """

    def __init__(
        self,
        directory: str | Path,
        allowed_series: frozenset[str] | set[str] = DEFAULT_SERIES,
        instrument_type: str = EQUITY_INSTRUMENT_TYPE,
    ):
        self.directory = Path(directory)
        self.allowed_series = frozenset(s.upper() for s in allowed_series)
        self.instrument_type = instrument_type.upper()
        self._bars: dict[str, list[Bar]] | None = None
        self._liquidity: dict[str, list[LiquidityRecord]] | None = None
        self._files_read: list[str] = []
        self._dir_stamp: tuple[str, ...] | None = None

    # --- loading ----------------------------------------------------------

    def refresh(self) -> None:
        """Forces a reload on the next access.

        Rarely needed: the cache already invalidates itself when a file is
        added to or removed from the directory. Use this after editing a
        file in place, which `_dir_stamp` cannot detect.
        """
        self._bars = None
        self._liquidity = None
        self._files_read = []
        self._dir_stamp = None

    def _dir_stamp_now(self) -> tuple[str, ...]:
        """Fingerprint of WHICH files are in the directory.

        Deliberately the set of names rather than a timestamp. The
        directory's own mtime looks cheaper, but its resolution is coarse
        enough that adding and removing a file within the same tick is
        invisible — a cache that is usually correct and occasionally serves
        stale prices is worse than one that is simply correct.

        One directory scan with no per-file `stat()`, so this stays cheap
        enough to check on every access. It does not detect an existing
        file being edited in place; `refresh()` covers that.
        """
        try:
            return tuple(
                sorted(
                    p.name for p in self.directory.iterdir()
                    if p.is_file() and p.suffix.lower() in (".csv", ".zip")
                )
            )
        except OSError:
            return ()

    def _files(self) -> list[Path]:
        if not self.directory.is_dir():
            raise FileNotFoundError(f"bhavcopy directory not found: {self.directory}")
        found = sorted(
            p for p in self.directory.iterdir()
            if p.is_file() and p.suffix.lower() in (".csv", ".zip")
        )
        if not found:
            raise FileNotFoundError(
                f"no .csv or .csv.zip bhavcopy files in {self.directory}. Download "
                "'CM-UDiFF Common Bhavcopy Final' from nseindia.com (All Reports) "
                "and place the files here."
            )
        return found

    def _load(self) -> None:
        # Read the stamp BEFORE parsing. If a file lands mid-parse we record
        # the older stamp and reload next time, rather than recording the
        # newer one and never seeing the file we raced with.
        stamp = self._dir_stamp_now()
        if self._bars is not None and stamp == self._dir_stamp:
            return

        bars: dict[str, list[Bar]] = {}
        liquidity: dict[str, list[LiquidityRecord]] = {}
        files_read: list[str] = []

        for path in self._files():
            retrieved_at = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
            with _open_text(path) as stream:
                self._read_rows(stream, path, retrieved_at, bars, liquidity)
            files_read.append(path.name)

        for series in bars.values():
            series.sort(key=lambda b: b.timestamp)
        for records in liquidity.values():
            records.sort(key=lambda r: r.date)

        self._bars = bars
        self._liquidity = liquidity
        self._files_read = files_read
        self._dir_stamp = stamp

    def _read_rows(
        self,
        stream: io.TextIOBase,
        path: Path,
        retrieved_at: datetime,
        bars: dict[str, list[Bar]],
        liquidity: dict[str, list[LiquidityRecord]],
    ) -> None:
        reader = csv.DictReader(stream)
        header = {(h or "").strip() for h in (reader.fieldnames or [])}

        if not header:
            raise BhavcopyFormatError(f"{path.name}: file is empty")

        if LEGACY_MARKER_COLUMNS & header:
            raise BhavcopyFormatError(
                f"{path.name}: this is a LEGACY (pre-2024-07-08) bhavcopy, which this "
                "provider deliberately does not parse. NSE's UDiFF changeover renamed "
                "every column, and guessing at the old ones risks silently misreading "
                "prices. Use UDiFF files (header starts 'TradDt,BizDt,Sgmt,...')."
            )

        missing = REQUIRED_COLUMNS - header
        if missing:
            raise BhavcopyFormatError(
                f"{path.name}: not a UDiFF bhavcopy -- missing columns {sorted(missing)}"
            )

        source = f"nse-udiff-bhavcopy:{path.name}"
        seen_in_file: set[str] = set()

        for row_num, row in enumerate(reader, start=2):
            row = {(k or "").strip(): v for k, v in row.items()}

            if (row.get(COL_INSTRUMENT) or "").strip().upper() != self.instrument_type:
                continue
            if (row.get(COL_SERIES) or "").strip().upper() not in self.allowed_series:
                continue

            symbol = (row.get(COL_SYMBOL) or "").strip().upper()
            if not symbol:
                continue

            context = f"{path.name}:{row_num} ({symbol})"

            # One file is one trading date, so a repeated symbol means the
            # caller concatenated files or NSE published a duplicate. Either
            # way it would create two bars with identical timestamps, which
            # validate_series rejects later with a less obvious message.
            if symbol in seen_in_file:
                raise BhavcopyFormatError(
                    f"{context}: symbol appears twice in one bhavcopy file"
                )
            seen_in_file.add(symbol)

            timestamp = _parse_trade_date(row.get(COL_DATE, ""))
            open_ = _to_float(row.get(COL_OPEN, ""), COL_OPEN, context)
            high = _to_float(row.get(COL_HIGH, ""), COL_HIGH, context)
            low = _to_float(row.get(COL_LOW, ""), COL_LOW, context)
            close = _to_float(row.get(COL_CLOSE, ""), COL_CLOSE, context)
            volume = int(_to_float(row.get(COL_VOLUME, ""), COL_VOLUME, context))

            bars.setdefault(symbol, []).append(
                Bar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    source=source,
                    retrieved_at=retrieved_at,
                    interval="1d",
                )
            )

            # Turnover and trade count are optional in practice: a row can
            # carry prices while these are blank. A liquidity record is only
            # created when both are genuinely present, so a tradability
            # filter never mistakes a missing value for zero liquidity.
            raw_turnover = (row.get(COL_TURNOVER) or "").strip()
            raw_trades = (row.get(COL_TRADES) or "").strip()
            if raw_turnover and raw_trades:
                liquidity.setdefault(symbol, []).append(
                    LiquidityRecord(
                        symbol=symbol,
                        date=timestamp,
                        turnover=_to_float(raw_turnover, COL_TURNOVER, context),
                        trade_count=int(_to_float(raw_trades, COL_TRADES, context)),
                        volume=volume,
                        close=close,
                    )
                )

    # --- DataProvider interface -------------------------------------------

    def get_bars(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[Bar]:
        if interval != "1d":
            raise ValueError(
                f"bhavcopy is an end-of-day report and supplies only '1d' bars, "
                f"not '{interval}'. Use an intraday-capable provider instead."
            )

        self._load()
        assert self._bars is not None

        series = self._bars.get(symbol.upper(), [])
        if not series:
            return []

        return validate_series(self._slice(series, start, end, key=lambda b: b.timestamp))

    def get_latest_quote(self, symbol: str) -> None:
        """Always None. A bhavcopy is published after the close; presenting
        its last row as a quote would misrepresent end-of-day data as live."""
        return None

    # --- liquidity (tradability-filter input) -----------------------------

    def get_liquidity(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[LiquidityRecord]:
        """Per-day turnover and trade count, for tradability filtering.

        Returns an empty list when the symbol is absent or its rows carried
        no turnover. Callers must treat empty as "liquidity unknown" and
        apply a conservative fallback, never as "illiquid" or as zero.
        """

        self._load()
        assert self._liquidity is not None

        records = self._liquidity.get(symbol.upper(), [])
        return self._slice(records, start, end, key=lambda r: r.date)

    @staticmethod
    def _slice(items, start: str | None, end: str | None, key):
        if start is not None:
            start_ts = _parse_trade_date(start)
            items = [i for i in items if key(i) >= start_ts]
        if end is not None:
            end_ts = _parse_trade_date(end)
            items = [i for i in items if key(i) <= end_ts]
        return list(items)

    # --- introspection ----------------------------------------------------

    def available_symbols(self) -> list[str]:
        """Every symbol present across the loaded files. This is the raw
        universe BEFORE any tradability filtering."""
        self._load()
        assert self._bars is not None
        return sorted(self._bars)

    def files_loaded(self) -> list[str]:
        self._load()
        return list(self._files_read)
