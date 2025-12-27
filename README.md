## Polymarket trading system (Python, asyncio)

Production-oriented scaffold for a Polymarket CLOB trader focused on **high-liquidity / high-volume** markets, supporting:
- **Paper trading end-to-end** (default)
- **Dynamic market discovery + ranking** (24h volume + liquidity thresholds)
- Two strategies:
  - **Cross-venue fair value** vs a pluggable external odds provider (stubbed + mock included)
  - **Market-making / spread capture** with inventory-aware skew and cancel/replace
- **Risk controls**: per-market position, per-event exposure, daily loss limit, kill-switch, feed lag/spread circuit breaker, time-based stop
- **Structured JSON logs**
- **SQLite persistence** for orders/fills + “paper tape” (books/trades) + position/PnL snapshots
- **Backtest runner** that replays recorded tape from SQLite

### Repo layout
- `config/`: env-driven settings
- `connectors/`
  - `polymarket/`: market discovery + WS stream (best-effort) + mock stream
  - `external_odds/`: provider interface + mock provider
- `strategies/`: cross-venue FV + market-making
- `risk/`: portfolio + risk engine
- `execution/`: paper broker + live broker stub (disabled)
- `monitoring/`: CLI dashboard
- `storage/`: SQLite persistence
- `main.py`: entrypoint

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Run: market scanner only

```bash
RUN_MODE=scanner TRADE_MODE=paper python main.py
```

### Run: paper trader (scanner + feed + strategies + paper execution)

```bash
RUN_MODE=paper TRADE_MODE=paper python main.py
```

By default, the system runs with a **mock market-data feed** so it is runnable without live connectivity. It records a tape (`tape` table) into `SQLITE_PATH`.

To attempt live WebSocket streaming (best-effort, schema may require adjustment):

```bash
USE_LIVE_WS_FEED=1 RUN_MODE=paper TRADE_MODE=paper python main.py
```

### Run: replay backtest from recorded tape

```bash
RUN_MODE=backtest TRADE_MODE=paper python main.py
```

Backtest controls:
- `BACKTEST_SPEED`: replay speed multiplier (e.g. 50.0)
- `BACKTEST_START_TS` / `BACKTEST_END_TS`: optional unix timestamps

### Tests

```bash
pytest -q
```

### Safety / live trading

`TRADE_MODE=live` is **disabled by default** and `execution/live.py` currently refuses to run. Implement `LiveBroker` with the official `py-clob-client` once you are satisfied with paper trading and risk controls.

