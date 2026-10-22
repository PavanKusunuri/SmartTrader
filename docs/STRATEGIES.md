# Strategies

## Swing prototype — `swing_ma_crossover`

Implemented in [`app/strategies/swing.py`](../app/strategies/swing.py).
Operates on **daily bars**.

**All parameters are experimental defaults, not tuned, and not a claim of
profitability.** See [RISK_AND_LIMITATIONS.md](RISK_AND_LIMITATIONS.md).

### Rules

| | Rule |
|---|---|
| Entry | Fast SMA crosses **above** slow SMA on the current bar (fast was `<=` slow on the prior bar, and is `>` slow now) |
| Exit | Fast SMA crosses **below** slow SMA, **or** the position has been held `>= max_holding_days` |
| Stop/target | Set at entry as a percentage of entry close, recorded on the position. **Not enforced by the Milestone 1 engine** — see limitations doc |
| Position limit | One position per symbol at a time |

### Parameters (`app/config.py` → `SwingStrategyConfig`)

| Parameter | Default | Meaning |
|---|---|---|
| `fast_ma_period` | 20 | Fast SMA lookback in bars |
| `slow_ma_period` | 50 | Slow SMA lookback in bars (validated to exceed fast) |
| `min_holding_days` | 3 | Reserved for future use; not yet enforced |
| `max_holding_days` | 20 | Forced exit after this many days held |
| `stop_loss_pct` | 0.05 | Stop level = entry close × (1 − this) |
| `target_pct` | 0.10 | Target level = entry close × (1 + this) |

### `NO_TRADE` conditions

The strategy returns `NO_TRADE` (with a human-readable reason and the inputs
that caused it) when:

- The latest bar's data quality is not `OK` (stale, invalid, or suspect gap).
- There is insufficient history (`< slow_ma_period + 1` bars) to evaluate a crossover.
- No crossover occurred and no position is open.
- A position is open but no exit condition is met.

### Causal correctness

`evaluate(bars, position)` receives only bars dated on or before the decision
day; `bars[-1]` is "now". Moving averages are computed from that slice only.
The prior-bar MA state used for crossover detection is computed from
`bars[:-1]`, never from future data. This is covered by
`tests/test_swing_strategy.py::test_no_look_ahead_future_bars_dont_affect_signal`
and `tests/test_backtest_engine.py`.

## Intraday prototype — `intraday_ma_momentum`

Implemented in [`app/strategies/intraday.py`](../app/strategies/intraday.py).
Operates on intraday bars (the bundled samples are 5-minute).

**Experimental defaults, not a profitability claim.** On the synthetic sample
data this prototype currently loses money net of assumed costs — see
[RISK_AND_LIMITATIONS.md](RISK_AND_LIMITATIONS.md).

### Rules

Entry requires **all four**:

| # | Condition |
|---|---|
| 1 | Trend filter: fast MA > slow MA |
| 2 | Momentum: current close > previous close |
| 3 | Volume confirmation: volume > `volume_surge_multiple` x average volume |
| 4 | Session: timestamp is inside the configured entry window |

Exit: trend filter breaks (fast MA <= slow MA). Stop-loss, target, and the
forced end-of-session flatten are enforced by the **engine**, not the
strategy — the engine owns fill simulation.

### Parameters (`app/config.py` → `IntradayStrategyConfig`)

| Parameter | Default | Meaning |
|---|---|---|
| `fast_ma_period` | 9 | Fast SMA lookback in bars |
| `slow_ma_period` | 21 | Slow SMA lookback (validated to exceed fast) |
| `volume_ma_period` | 20 | Average-volume lookback |
| `volume_surge_multiple` | 1.5 | Required volume multiple vs that average |
| `stop_loss_pct` | 0.01 | Stop = entry close × (1 − this) |
| `target_pct` | 0.02 | Target = entry close × (1 + this) |

### Session rules (`app/market_calendar.py` → `MarketSession`)

| Setting | Default | Effect |
|---|---|---|
| `open_time` / `close_time` | 09:15 / 15:30 | Session bounds (**unverified assumption**) |
| `entry_cutoff` | 14:30 | No **new** entries at or after this time |
| `flatten_time` | 15:15 | Open positions force-closed — no overnight risk |
| `holidays` | **empty** | You must supply your own; only weekends excluded by default |

## Honest comparison between the two

The two strategies run on different bar intervals and so face different cost
and slippage burdens. They are **not** directly comparable, and
`python -m app.cli compare` says so in its output rather than quietly ranking
them. It refuses to declare a winner unless each strategy has >= 30 trades
and the net-return margin is > 2%.

Run it yourself:

```powershell
python -m app.cli compare
python -m app.cli evaluate --symbols SAMPLE_TCS --mode oos
python -m app.cli evaluate --symbols SAMPLE_INTRADAY_TCS --strategy intraday --interval 5m --mode oos
```
