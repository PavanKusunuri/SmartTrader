from pathlib import Path

import pytest

from app.data.csv_provider import CsvProvider
from app.models import DataQuality

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def test_loads_bundled_sample_data():
    provider = CsvProvider(SAMPLES_DIR)
    bars = provider.get_bars("SAMPLE_TCS")
    assert len(bars) == 180
    assert bars[0].symbol == "SAMPLE_TCS"
    assert bars[0].source == "csv:SAMPLE_TCS.csv"
    assert all(b.timestamp.tzinfo is not None for b in bars)
    assert bars == sorted(bars, key=lambda b: b.timestamp)


def test_bars_are_validated_not_raw():
    provider = CsvProvider(SAMPLES_DIR)
    bars = provider.get_bars("SAMPLE_INFY")
    # Synthetic data is clean by construction; every bar should pass validation.
    assert all(b.quality == DataQuality.OK for b in bars)


def test_start_end_filtering():
    provider = CsvProvider(SAMPLES_DIR)
    bars = provider.get_bars("SAMPLE_TCS", start="2024-02-01", end="2024-02-29")
    assert len(bars) > 0
    assert all("2024-02" in b.timestamp.date().isoformat() for b in bars)


def test_unknown_symbol_raises():
    provider = CsvProvider(SAMPLES_DIR)
    with pytest.raises(FileNotFoundError):
        provider.get_bars("DOES_NOT_EXIST")


def test_missing_required_column_raises(tmp_path):
    bad_csv = tmp_path / "BAD.csv"
    bad_csv.write_text("date,open,high,low,close\n2024-01-01,1,2,0.5,1.5\n", encoding="utf-8")
    provider = CsvProvider(tmp_path)
    with pytest.raises(ValueError, match="missing required columns"):
        provider.get_bars("BAD")
