"""Background sync while the app runs: shortly after start-up, then whenever a connection's last sync is older than
the interval. The app only runs when the user starts it, so this is "daily" in practice; manual syncs go through
``POST /banking/sync``."""

from __future__ import annotations

import asyncio
import logging

from src.banking.sync import BankSync

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 30
CHECK_EVERY_SECONDS = 1800


class BankSyncScheduler:
    def __init__(self, make_sync, enabled: bool, initial_delay: float = INITIAL_DELAY_SECONDS, check_every: float = CHECK_EVERY_SECONDS):
        self._make_sync = make_sync
        self.enabled = enabled
        self.initial_delay = initial_delay
        self.check_every = check_every
        self._task: asyncio.Task | None = None
        self.lock = asyncio.Lock()  # a manual sync and the scheduler never run at the same time

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="bank-sync")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(self.initial_delay)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Bank sync: scheduled run failed")
            await asyncio.sleep(self.check_every)

    async def tick(self):
        async with self.lock:
            sync: BankSync = self._make_sync()
            reports = await sync.sync_due()
            if reports:
                logger.info("Bank sync: %d connection(s) synced (%s)", len(reports), ", ".join(f"{r.connection_id}:{r.status}" for r in reports))
            return reports
