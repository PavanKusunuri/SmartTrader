"""Import manually downloaded NSE UDiFF bhavcopy files (CSV or ZIP).

DAILY END-OF-DAY DATA ONLY. It supports swing research; it cannot validate
intraday entries or exits and must never stand in for 5m/15m candles.

Pipeline, per input file:
  1. Copy the file UNCHANGED into `raw/` (sha256 recorded in the manifest).
  2. Parse + validate it with the verified UDiFF reader.
  3. Write normalized per-symbol daily CSVs into `processed/` (the
     `date,open,high,low,close,volume` layout `CsvProvider` already reads),
     merged with anything imported earlier.
  4. Write a manifest JSON into `manifests/`.

There is deliberately NO network code here. The project's existing decision
(docs/DATA_SOURCES.md, CLAUDE.md section 6) is that NSE files are downloaded
by hand; NSE blocks automation and this app does not try to bypass that.

Nothing is ever filled in: a missing session stays missing and is reported.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from app.data.bhavcopy_provider import BhavcopyFormatError, BhavcopyProvider
from app.data.validation import validate_bar_fields
from app.market_calendar import MarketSession
from app.models import Bar

IST = ZoneInfo("Asia/Kolkata")
PARSER_VERSION = "udiff-import-1"
SOURCE_LABEL = "NSE CM-UDiFF Common Bhavcopy Final (manual download)"
DATASET_LABEL = "DAILY end-of-day data (interval 1d) - NOT intraday"
PROCESSED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass
class ImportReport:
    files_ok: list[str] = field(default_factory=list)
    files_failed: dict[str, str] = field(default_factory=dict)
    rows_imported: int = 0
    rows_skipped_duplicate: int = 0
    symbols: set[str] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)
    missing_sessions: list[date] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest_path: Path | None = None

    def summary(self) -> str:
        cov = f"{min(self.dates)} .. {max(self.dates)}" if self.dates else "none"
        lines = [
            f"Dataset      : {DATASET_LABEL}",
            f"Files ok     : {len(self.files_ok)}   failed: {len(self.files_failed)}",
            f"Rows imported: {self.rows_imported} "
            f"(duplicates skipped: {self.rows_skipped_duplicate})",
            f"Symbols      : {len(self.symbols)}",
            f"Date coverage: {cov} ({len(self.dates)} session(s))",
            f"Missing sessions (weekdays not in data, minus known holidays): "
            f"{len(self.missing_sessions)}",
        ]
        lines += [f"  FAILED {n}: {why}" for n, why in self.files_failed.items()]
        lines += [f"  warning: {w}" for w in self.warnings]
        if self.manifest_path:
            lines.append(f"Manifest     : {self.manifest_path}")
        return "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expand_inputs(inputs: Iterable[Path]) -> list[Path]:
    """Directories expand to their .csv/.zip files; files pass through."""
    out: list[Path] = []
    for p in inputs:
        p = Path(p)
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in (".csv", ".zip"))
        else:
            out.append(p)
    return out


def _parse_one(path: Path) -> list[Bar]:
    """Parse a single file via BhavcopyProvider in an isolated temp dir view.

    Reuses the verified UDiFF reader rather than duplicating its column logic.
    Corrupt zips are converted to BhavcopyFormatError.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy2(path, Path(tmp) / path.name)
        provider = BhavcopyProvider(tmp)
        try:
            provider._load()  # noqa: SLF001 - intentional: parse-only use
        except zipfile.BadZipFile as exc:
            raise BhavcopyFormatError(f"{path.name}: corrupt or incomplete ZIP ({exc})") from exc
        bars = [b for s in provider._bars.values() for b in s]  # noqa: SLF001
    if not bars:
        raise BhavcopyFormatError(
            f"{path.name}: no EQ equity rows found (wrong report, or empty/truncated file)"
        )
    return bars


def _read_processed(path: Path) -> dict[str, tuple[str, str, str, str, str]]:
    rows: dict[str, tuple[str, str, str, str, str]] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["date"]] = (r["open"], r["high"], r["low"], r["close"], r["volume"])
    return rows


def import_bhavcopy_files(
    inputs: Iterable[Path],
    raw_dir: Path,
    processed_dir: Path,
    manifest_dir: Path,
    start: date | None = None,
    end: date | None = None,
    symbols: Iterable[str] | None = None,
    session: MarketSession | None = None,
    progress: Callable[[str], None] | None = None,
) -> ImportReport:
    """Import official UDiFF files. See module docstring for the pipeline."""
    say = progress or (lambda _m: None)
    session = session or MarketSession()
    wanted = {s.upper() for s in symbols} if symbols else None
    report = ImportReport()
    raw_dir, processed_dir, manifest_dir = Path(raw_dir), Path(processed_dir), Path(manifest_dir)
    for d in (raw_dir, processed_dir, manifest_dir):
        d.mkdir(parents=True, exist_ok=True)

    files = expand_inputs(inputs)
    file_records: list[dict] = []
    per_symbol: dict[str, dict[str, tuple[str, str, str, str, str]]] = {}
    seen_dates: dict[date, str] = {}
    retrieved_at = datetime.now(tz=IST)

    for i, path in enumerate(files, 1):
        say(f"[{i}/{len(files)}] {path.name}")
        if not path.is_file():
            report.files_failed[path.name] = "file not found"
            continue
        try:
            bars = _parse_one(path)
        except (BhavcopyFormatError, zipfile.BadZipFile, OSError, UnicodeError) as exc:
            report.files_failed[path.name] = str(exc)
            continue

        file_dates = {b.timestamp.date() for b in bars}
        if len(file_dates) != 1:
            report.files_failed[path.name] = (
                f"expected one trading date per file, found {sorted(map(str, file_dates))}"
            )
            continue
        day = next(iter(file_dates))
        if (start and day < start) or (end and day > end):
            report.warnings.append(f"{path.name}: date {day} outside requested range; skipped")
            continue
        if day in seen_dates:
            report.rows_skipped_duplicate += len(bars)
            report.warnings.append(
                f"{path.name}: trading date {day} already imported from {seen_dates[day]}; skipped"
            )
            continue

        # Validate OHLC per row; invalid rows are excluded, never repaired.
        good: list[Bar] = []
        for b in bars:
            problems = validate_bar_fields(b)
            if problems:
                report.warnings.append(f"{path.name} {b.symbol}: dropped row ({'; '.join(problems)})")
            else:
                good.append(b)

        raw_copy = raw_dir / path.name
        if not raw_copy.exists():
            shutil.copy2(path, raw_copy)  # original bytes, unchanged
        seen_dates[day] = path.name

        kept = 0
        for b in good:
            if wanted and b.symbol not in wanted:
                continue
            per_symbol.setdefault(b.symbol, {})[day.isoformat()] = (
                repr(b.open), repr(b.high), repr(b.low), repr(b.close), str(b.volume),
            )
            kept += 1
        report.files_ok.append(path.name)
        report.dates.add(day)
        file_records.append(
            {"file": path.name, "sha256": _sha256(path), "trading_date": day.isoformat(),
             "rows_kept": kept}
        )

    # Merge into processed per-symbol CSVs (existing rows win on duplicate dates).
    for sym, new_rows in per_symbol.items():
        out = processed_dir / f"{sym}.csv"
        merged = _read_processed(out)
        for d, vals in new_rows.items():
            if d in merged:
                report.rows_skipped_duplicate += 1
            else:
                merged[d] = vals
                report.rows_imported += 1
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(PROCESSED_COLUMNS)
            for d in sorted(merged):
                w.writerow([d, *merged[d]])
        report.symbols.add(sym)

    if report.dates:
        d, last = min(report.dates), max(report.dates)
        while d <= last:
            if session.is_trading_day(d) and d not in report.dates:
                report.missing_sessions.append(d)
            d = date.fromordinal(d.toordinal() + 1)
        if report.missing_sessions:
            report.warnings.append(
                f"{len(report.missing_sessions)} weekday(s) have no file. If they were exchange "
                "holidays supply a holidays CSV; otherwise download the missing days. "
                "Nothing was filled in."
            )
        if not session.holidays:
            report.warnings.append("no holiday calendar supplied: holidays may show as missing sessions")

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%S")
    manifest = {
        "dataset": DATASET_LABEL,
        "source": SOURCE_LABEL,
        "source_urls": [
            "https://www.nseindia.com/all-reports",
            "https://www.nseindia.com/static/resources/historical-reports-capital-market-daily-monthly-archives",
        ],
        "acquisition": "manual download by the user; no automated fetch",
        "imported_at": retrieved_at.isoformat(),
        "parser_version": PARSER_VERSION,
        "requested_range": {"start": start.isoformat() if start else None,
                            "end": end.isoformat() if end else None},
        "symbol_filter": sorted(wanted) if wanted else "all",
        "files": file_records,
        "failed_files": report.files_failed,
        "successful_dates": sorted(d.isoformat() for d in report.dates),
        "missing_sessions": [d.isoformat() for d in report.missing_sessions],
        "rows_imported": report.rows_imported,
        "symbols": len(report.symbols),
        "prices_adjusted_for_corporate_actions": False,
        "warnings": report.warnings,
    }
    report.manifest_path = manifest_dir / f"import_{stamp}.json"
    report.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report
