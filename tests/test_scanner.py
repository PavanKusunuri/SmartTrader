from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import AppConfig, SwingStrategyConfig
from app.data.base import DataProvider
from app.market_calendar import MarketSession
from app.models import Bar, DataQuality, OrderStatus, Signal, SignalAction
from app.paper.broker import PaperBroker
from app.scanner.scanner import ScanOutcome, Scanner
from app.scanner.universe import InvalidWatchlist, Watchlist
from app.strategies.base import Strategy

IST = ZoneInfo("Asia/Kolkata")

# 2024-01-02 is a Tuesday.
TRADING_DAY = datetime(2024, 1, 2, 11, 0, tzinfo=IST)


class ListProvider(DataProvider):
    def __init__(self, bars_by_symbol, raise_for=None):
        self.bars_by_symbol = bars_by_symbol
        self.raise_for = raise_for or set()
        self.call_count = 0

    def get_bars(self, symbol, interval="1d", start=None, end=None):
        self.call_count += 1
        if symbol in self.raise_for:
            raise ConnectionError(f"simulated provider outage for {symbol}")
        return list(self.bars_by_symbol.get(symbol, []))


class AlwaysBuyStrategy(Strategy):
    name = "always_buy"

    @property
    def required_history(self) -> int:
        return 1

    def evaluate(self, bars, position=None):
        bar = bars[-1]
        if position is not None:
            return Signal(
                signal_id=f"ab:{bar.symbol}:{bar.timestamp.isoformat()}",
                symbol=bar.symbol, timestamp=bar.timestamp, strategy_name=self.name,
                action=SignalAction.NO_TRADE, reason="already holding", inputs={},
            )
        return Signal(
            signal_id=f"ab:{bar.symbol}:{bar.timestamp.isoformat()}",
            symbol=bar.symbol, timestamp=bar.timestamp, strategy_name=self.name,
            action=SignalAction.BUY, reason="always buys (test double)", inputs={},
            stop_loss=round(bar.close * 0.95, 2), target=round(bar.close * 1.1, 2),
        )


class ExplodingStrategy(Strategy):
    name = "exploding"

    def evaluate(self, bars, position=None):
        raise RuntimeError("strategy blew up")


def bar_at(symbol: str, ts: datetime, close: float = 100.0, quality=DataQuality.OK) -> Bar:
    return Bar(
        symbol=symbol, timestamp=ts, open=close, high=close + 1, low=close - 1,
        close=close, volume=1000, source="test-double", retrieved_at=ts,
        interval="1d", quality=quality,
    )


def make_scanner(bars_by_symbol, symbols, strategy=None, session=None, raise_for=None,
                 max_bar_age=timedelta(days=5)):
    config = AppConfig(swing=SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4))
    broker = PaperBroker(config)
    return Scanner(
        provider=ListProvider(bars_by_symbol, raise_for=raise_for),
        strategy=strategy or AlwaysBuyStrategy(),
        broker=broker,
        watchlist=Watchlist.from_list(symbols),
        session=session,
        max_bar_age=max_bar_age,
    )


# --- Watchlist --------------------------------------------------------------


def test_watchlist_normalises_and_rejects_bad_input():
    assert Watchlist.from_list([" tcs ", "infy"]).symbols == ("TCS", "INFY")

    with pytest.raises(InvalidWatchlist, match="empty"):
        Watchlist.from_list([])
    with pytest.raises(InvalidWatchlist, match="duplicate"):
        Watchlist.from_list(["TCS", "TCS"])
    with pytest.raises(InvalidWatchlist, match="cap"):
        Watchlist.from_list([f"SYM{i}" for i in range(51)])


def test_watchlist_membership_and_iteration():
    wl = Watchlist.from_list(["TCS", "INFY"])
    assert "TCS" in wl and "tcs" in wl
    assert "RELIANCE" not in wl
    assert len(wl) == 2
    assert list(wl) == ["TCS", "INFY"]


def test_watchlist_from_csv(tmp_path):
    path = tmp_path / "wl.csv"
    path.write_text("symbol\nTCS\nINFY\n", encoding="utf-8")
    assert Watchlist.from_csv(path).symbols == ("TCS", "INFY")


def test_watchlist_csv_errors_are_loud(tmp_path):
    with pytest.raises(InvalidWatchlist, match="not found"):
        Watchlist.from_csv(tmp_path / "nope.csv")

    bad = tmp_path / "bad.csv"
    bad.write_text("ticker\nTCS\n", encoding="utf-8")
    with pytest.raises(InvalidWatchlist, match="missing required 'symbol' column"):
        Watchlist.from_csv(bad)

    empty = tmp_path / "empty.csv"
    empty.write_text("symbol\n\n", encoding="utf-8")
    with pytest.raises(InvalidWatchlist, match="no symbols"):
        Watchlist.from_csv(empty)


# --- Core scanning ----------------------------------------------------------


def test_scan_creates_simulated_order_on_fresh_data():
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"])
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.TRADED) == 1
    assert len(report.orders_created) == 1
    assert report.orders_created[0].status == OrderStatus.FILLED
    assert report.warnings == []


def test_rescanning_the_same_bar_does_not_duplicate_orders():
    """Repeated polling must be idempotent (CLAUDE.md section 10)."""
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"])

    first = scanner.scan_once(TRADING_DAY)
    second = scanner.scan_once(TRADING_DAY + timedelta(minutes=5))
    third = scanner.scan_once(TRADING_DAY + timedelta(minutes=10))

    assert first.count(ScanOutcome.TRADED) == 1
    assert second.count(ScanOutcome.SKIPPED_DUPLICATE) == 1
    assert third.count(ScanOutcome.SKIPPED_DUPLICATE) == 1
    # Exactly one fill across all three scans.
    filled = [o for o in scanner.broker.orders if o.status == OrderStatus.FILLED]
    assert len(filled) == 1


def test_stale_data_is_rejected_not_traded():
    """After an outage, old bars must not become a burst of entries."""
    old_bar = [bar_at("TCS", TRADING_DAY - timedelta(days=30))]
    scanner = make_scanner({"TCS": old_bar}, ["TCS"], max_bar_age=timedelta(days=5))
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.REJECTED_STALE) == 1
    assert report.orders_created == []
    assert any("stale" in w for w in report.warnings)


def test_many_stale_symbols_produce_no_order_burst():
    symbols = [f"SYM{i}" for i in range(10)]
    bars = {s: [bar_at(s, TRADING_DAY - timedelta(days=60))] for s in symbols}
    scanner = make_scanner(bars, symbols, max_bar_age=timedelta(days=5))
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.REJECTED_STALE) == 10
    assert report.orders_created == []
    assert len(report.warnings) == 10


def test_invalid_bar_never_becomes_a_signal():
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1), quality=DataQuality.INVALID)]
    scanner = make_scanner({"TCS": bars}, ["TCS"])
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.REJECTED_DATA) == 1
    assert report.orders_created == []


def test_future_bars_are_not_visible_to_the_decision():
    """No look-ahead: a bar dated after `now` must be ignored."""
    bars = [
        bar_at("TCS", TRADING_DAY - timedelta(days=1), close=100),
        bar_at("TCS", TRADING_DAY + timedelta(days=1), close=999),  # future
    ]
    scanner = make_scanner({"TCS": bars}, ["TCS"])
    report = scanner.scan_once(TRADING_DAY)

    result = report.results[0]
    assert result.bar_timestamp == TRADING_DAY - timedelta(days=1)
    # Fill must be based on the visible bar's close (100), not the future 999.
    assert result.order.filled_price < 110


def test_no_bars_at_all_is_reported_not_crashed():
    scanner = make_scanner({"TCS": []}, ["TCS"])
    report = scanner.scan_once(TRADING_DAY)
    assert report.count(ScanOutcome.REJECTED_DATA) == 1


def test_only_future_bars_is_rejected():
    bars = [bar_at("TCS", TRADING_DAY + timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"])
    report = scanner.scan_once(TRADING_DAY)
    assert report.count(ScanOutcome.REJECTED_DATA) == 1


# --- Session gating ---------------------------------------------------------


def test_closed_session_skips_all_signal_logic():
    session = MarketSession()
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"], session=session)

    # 03:00 on a trading day is outside 09:15-15:30.
    report = scanner.scan_once(datetime(2024, 1, 2, 3, 0, tzinfo=IST))
    assert report.count(ScanOutcome.SKIPPED_SESSION) == 1
    assert report.orders_created == []
    # The provider should not even be consulted when the market is shut.
    assert scanner.provider.call_count == 0


def test_weekend_scan_does_nothing():
    session = MarketSession()
    bars = [bar_at("TCS", datetime(2024, 1, 5, 11, 0, tzinfo=IST))]
    scanner = make_scanner({"TCS": bars}, ["TCS"], session=session)
    saturday = datetime(2024, 1, 6, 11, 0, tzinfo=IST)
    report = scanner.scan_once(saturday)
    assert report.count(ScanOutcome.SKIPPED_SESSION) == 1


def test_holiday_scan_does_nothing():
    session = MarketSession(holidays=frozenset({TRADING_DAY.date()}))
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"], session=session)
    report = scanner.scan_once(TRADING_DAY)
    assert report.count(ScanOutcome.SKIPPED_SESSION) == 1


def test_entry_after_cutoff_is_blocked_during_open_session():
    session = MarketSession(entry_cutoff=time(14, 30))
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"], session=session)
    late = datetime(2024, 1, 2, 15, 0, tzinfo=IST)  # in session, past cutoff
    report = scanner.scan_once(late)

    assert report.count(ScanOutcome.SKIPPED_SESSION) == 1
    assert report.orders_created == []


# --- Failure isolation ------------------------------------------------------


def test_provider_failure_is_isolated_to_one_symbol():
    bars = {
        "GOOD": [bar_at("GOOD", TRADING_DAY - timedelta(days=1))],
        "BAD": [bar_at("BAD", TRADING_DAY - timedelta(days=1))],
    }
    scanner = make_scanner(bars, ["GOOD", "BAD"], raise_for={"BAD"})
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.TRADED) == 1
    assert report.count(ScanOutcome.ERROR) == 1
    assert any("BAD" in w and "provider error" in w for w in report.warnings)


def test_strategy_exception_is_isolated_and_never_trades():
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = make_scanner({"TCS": bars}, ["TCS"], strategy=ExplodingStrategy())
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.ERROR) == 1
    assert report.orders_created == []
    assert any("strategy error" in w for w in report.warnings)


def test_kill_switch_stops_scanner_entries():
    config = AppConfig()
    config.risk.kill_switch_enabled = True
    broker = PaperBroker(config)
    bars = [bar_at("TCS", TRADING_DAY - timedelta(days=1))]
    scanner = Scanner(
        provider=ListProvider({"TCS": bars}), strategy=AlwaysBuyStrategy(),
        broker=broker, watchlist=Watchlist.from_list(["TCS"]),
    )
    report = scanner.scan_once(TRADING_DAY)

    order = report.results[0].order
    assert order.status == OrderStatus.REJECTED
    assert "kill switch" in order.rejection_reason


def test_second_symbol_still_scanned_after_first_is_stale():
    bars = {
        "STALE": [bar_at("STALE", TRADING_DAY - timedelta(days=60))],
        "FRESH": [bar_at("FRESH", TRADING_DAY - timedelta(days=1))],
    }
    scanner = make_scanner(bars, ["STALE", "FRESH"], max_bar_age=timedelta(days=5))
    report = scanner.scan_once(TRADING_DAY)

    assert report.count(ScanOutcome.REJECTED_STALE) == 1
    assert report.count(ScanOutcome.TRADED) == 1
