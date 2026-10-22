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

### Intraday prototype

Not implemented. Milestone 2. It needs session-boundary handling (entry
cutoff window, no new entries near close, forced end-of-day exit), which the
current engine does not model.
