## SuperSpreader — 5-Minute Chart Directional Paper Trader

Directional trading system for Polymarket CLOB markets, focused on **5-minute chart** technical analysis with **paper trading**. Uses proven methods:

- **EMA Crossover** (9/21) as primary entry signal
- **RSI (14)** confirmation to avoid overbought/oversold traps
- **VWAP** directional bias filter
- **50 EMA** trend filter for higher-timeframe alignment
- **ATR-based** stop loss, take profit, and trailing stops

### Architecture

```
Tick Feed (mock/gamma/ws) --> CandleAggregator (5m OHLCV) --> Indicators (EMA/RSI/VWAP/ATR)
                                                                    |
                                                              FiveMinuteChartStrategy
                                                                    |
                                                        Entry/Exit via PaperBroker
                                                                    |
                                                              SQLite + Dashboard
```

### Key components

| Module | Purpose |
|--------|---------|
| `trading/candles.py` | Aggregates tick stream into 5-minute OHLCV candles |
| `strategies/indicators.py` | EMA, RSI, VWAP, Bollinger Bands, ATR, MACD |
| `strategies/five_min_chart.py` | Directional strategy with confluence filters |
| `execution/paper.py` | Paper broker with realistic fill simulation |
| `risk/rules.py` | Pre-trade risk checks (position limits, daily loss limit) |
| `storage/sqlite.py` | Candles, signals, positions, PnL snapshots |

### Strategy logic

**Long entry** when all confluence filters agree:
1. 9 EMA crosses above 21 EMA
2. RSI(14) between 20-65 (not overbought)
3. Price above VWAP (bullish bias)
4. Price above 50 EMA (trend confirmation)

**Short entry** (mirror conditions):
1. 9 EMA crosses below 21 EMA
2. RSI(14) between 35-80 (not oversold)
3. Price below VWAP
4. Price below 50 EMA

**Exits** (first triggered wins):
- ATR-based stop loss (1.5x ATR from entry)
- ATR-based take profit (2.0x ATR from entry)
- Trailing stop (activates after 1x ATR profit, trails at 1x ATR distance)
- Counter-signal (opposite EMA crossover)
- Time-based (max 24 candles = 2 hours)

### Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env as needed, then:
python3 main.py --mode paper
```

### Configure

Key knobs in `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `CANDLE_INTERVAL_SECS` | 300 | Candle period (300 = 5 minutes) |
| `FIVE_MIN_FAST_EMA` | 9 | Fast EMA period |
| `FIVE_MIN_SLOW_EMA` | 21 | Slow EMA period |
| `FIVE_MIN_TREND_EMA` | 50 | Trend filter EMA |
| `FIVE_MIN_RSI_PERIOD` | 14 | RSI lookback |
| `FIVE_MIN_ATR_STOP_MULT` | 1.5 | Stop loss = ATR x this |
| `FIVE_MIN_ATR_TP_MULT` | 2.0 | Take profit = ATR x this |
| `FIVE_MIN_MAX_HOLD_CANDLES` | 24 | Max position hold time |
| `FIVE_MIN_REQUIRE_VWAP` | 1 | VWAP confluence filter |
| `FIVE_MIN_REQUIRE_TREND` | 1 | Trend EMA filter |
| `FIVE_MIN_REQUIRE_RSI` | 1 | RSI filter |

### Run modes

```bash
# Paper trader (5m chart strategy)
python3 main.py --mode paper

# Scanner only (market discovery)
python3 main.py --mode scanner

# Backtest (replay tape data)
python3 main.py --mode backtest
```

### Dashboard

- `http://127.0.0.1:8000/` (configurable via `DASHBOARD_HOST` / `DASHBOARD_PORT`)

### Tests

```bash
python3 -m pytest tests/ -v
```

### Notes / safety

- This is **paper trading only**. It consumes live public data, simulates fills locally, and never sends live orders.
- To validate signal rate without fills: set `EXECUTION_MODE=shadow`.
- Confluence filters can be toggled off individually for testing (set `FIVE_MIN_REQUIRE_*=0`).
- All indicator computations are pure functions with no external dependencies.
