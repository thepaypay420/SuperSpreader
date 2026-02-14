## SuperSpreader — BTC 5-Minute Up/Down Paper Trader

Directional paper trading system targeting **Polymarket BTC Up/Down 5-minute binary markets**. Uses proven technical analysis on real BTC/USD price data to predict whether Bitcoin will go up or down in the next 5-minute window.

**Target market**: [BTC Up or Down 5m](https://polymarket.com/event/btc-updown-5m) ($8.9M+ daily volume)

### How it works

```
Binance BTC/USD ──> CandleAggregator (5m OHLCV) ──> Indicators (EMA/RSI/VWAP/ATR)
                                                           |
                                                    BtcUpDownStrategy
                                                           |
                                              Direction + Confidence Score
                                                           |
                                           ┌───────────────┴───────────────┐
                                           │                               │
                                      BUY "Up" token              BUY "Down" token
                                      (if bullish)                 (if bearish)
                                           │                               │
                                           └───────────────┬───────────────┘
                                                           |
                                                    PaperBroker ──> SQLite
```

1. **BTC price feed** streams real BTC/USD from Binance every 5 seconds
2. **Candle aggregator** builds 5-minute OHLCV bars (bootstraps 200 candles from Binance klines on startup)
3. **Technical indicators** computed on the candle history: EMA(9/21/50), RSI(14), VWAP, ATR(14)
4. **BTC market discovery** finds the next active "BTC Up or Down" 5m event on Polymarket
5. **Direction + confidence** evaluated from indicator confluence
6. **Bet placed** if confidence exceeds threshold (default 55%)
7. **Market resolves** in 5 minutes; position auto-settles

### Signal generation

| Indicator | Bullish (Up) | Bearish (Down) |
|-----------|-------------|----------------|
| EMA(9) vs EMA(21) | Fast > Slow | Fast < Slow |
| RSI(14) | 50-70 (not overbought) | 30-50 (not oversold) |
| VWAP | Price above VWAP | Price below VWAP |
| EMA(50) trend | Price above 50 EMA | Price below 50 EMA |
| EMA crossover | Bonus if just crossed up | Bonus if just crossed down |

**Confidence scoring**: Each confirming indicator adds to the confidence score (0.50 base). A bet is only placed when confidence exceeds the configurable threshold.

### Project structure

| Module | Purpose |
|--------|---------|
| `connectors/btc_price_feed.py` | Real BTC/USD from Binance + CoinGecko |
| `connectors/polymarket/btc_5m_discovery.py` | Discovers next active BTC 5m up/down event |
| `trading/candles.py` | Aggregates tick stream into 5m OHLCV candles |
| `strategies/indicators.py` | EMA, RSI, VWAP, Bollinger Bands, ATR, MACD |
| `strategies/btc_updown.py` | BTC Up/Down strategy with confidence scoring |
| `strategies/five_min_chart.py` | Generic 5m chart strategy (also available) |
| `execution/paper.py` | Paper broker with realistic fill simulation |
| `risk/rules.py` | Pre-trade risk checks |
| `storage/sqlite.py` | Candles, signals, positions, PnL snapshots |

### Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
python3 main.py --mode paper
```

### Configure

Key settings in `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_BTC_UPDOWN` | 1 | Enable BTC 5m up/down strategy |
| `BTC_PRICE_SOURCE` | live | `live` (Binance) or `mock` (synthetic) |
| `BTC_PRICE_POLL_SECS` | 5 | BTC price poll interval |
| `BTC_UPDOWN_MIN_CONFIDENCE` | 0.55 | Min confidence to place a bet (0-1) |
| `BTC_BOOTSTRAP_KLINES` | 1 | Bootstrap candle history from Binance on startup |
| `CANDLE_INTERVAL_SECS` | 300 | Candle period (300 = 5 minutes) |
| `FIVE_MIN_FAST_EMA` | 9 | Fast EMA period |
| `FIVE_MIN_SLOW_EMA` | 21 | Slow EMA period |
| `FIVE_MIN_TREND_EMA` | 50 | Trend filter EMA |
| `FIVE_MIN_RSI_PERIOD` | 14 | RSI lookback |
| `BASE_ORDER_SIZE` | 10 | USDC per bet |
| `FIVE_MIN_REQUIRE_VWAP` | 1 | VWAP confluence filter |
| `FIVE_MIN_REQUIRE_TREND` | 1 | Trend EMA filter |
| `FIVE_MIN_REQUIRE_RSI` | 1 | RSI filter |

### Run modes

```bash
# Paper trader (BTC up/down + optional generic 5m)
python3 main.py --mode paper

# Scanner only (market discovery)
python3 main.py --mode scanner

# Backtest (replay tape data)
python3 main.py --mode backtest
```

### Dashboard

`http://127.0.0.1:8000/` (configurable via `DASHBOARD_HOST` / `DASHBOARD_PORT`)

### Tests

```bash
python3 -m pytest tests/ -v
```

78 tests covering candles, indicators, strategy logic, discovery, and storage.

### Safety

- **Paper trading only**. Consumes live public data, simulates fills locally, never sends live orders.
- Confluence filters can be toggled off individually for testing (`FIVE_MIN_REQUIRE_*=0`).
- All indicator computations are pure functions with no external dependencies.
- To validate signal rate without fills: set `EXECUTION_MODE=shadow`.
