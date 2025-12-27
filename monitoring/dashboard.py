from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.console import Console
from rich.table import Table

from utils.logging import get_logger


async def run_dashboard_task(settings: Any, store: Any) -> None:
    """
    Lightweight CLI dashboard (optional). Safe to run alongside any mode.
    """
    log = get_logger(__name__)
    console = Console()

    # Avoid noisy UI in scanner-only mode unless explicitly desired
    enabled = True
    if settings.run_mode == "scanner":
        enabled = False

    if not enabled:
        while True:
            await asyncio.sleep(3600)

    while True:
        try:
            pnl = store.fetch_latest_pnl() or {}
            positions = store.fetch_latest_positions(limit=20)

            table = Table(title=f"Polymarket Trader ({settings.trade_mode}/{settings.run_mode}) @ {time.strftime('%X')}")
            table.add_column("market_id", overflow="fold")
            table.add_column("pos", justify="right")
            table.add_column("avg", justify="right")
            table.add_column("mark", justify="right")
            table.add_column("uPnL", justify="right")
            table.add_column("rPnL", justify="right")

            for p in positions:
                table.add_row(
                    str(p["market_id"]),
                    f'{p["position"]:.2f}',
                    f'{p["avg_price"]:.3f}',
                    f'{p["mark_price"]:.3f}',
                    f'{p["unrealized_pnl"]:.2f}',
                    f'{p["realized_pnl"]:.2f}',
                )

            console.clear()
            console.print(table)
            console.print(
                f'Total PnL: {pnl.get("total_pnl", 0.0):.2f} (u={pnl.get("total_unrealized", 0.0):.2f}, r={pnl.get("total_realized", 0.0):.2f})'
            )
        except Exception:
            log.exception("dashboard.error")

        await asyncio.sleep(1.0)

