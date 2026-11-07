import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

Tick = Callable[[], Awaitable[None]]


async def _noop_tick() -> None:
    """Placeholder until the jobs queue (FOR UPDATE SKIP LOCKED) lands in M1."""


async def run_worker(
    stop: asyncio.Event, *, tick: Tick = _noop_tick, poll_interval: float = 1.0
) -> int:
    """Runs `tick` until `stop` is set. Returns the number of ticks run."""
    log.info("worker.started", poll_interval=poll_interval)
    ticks = 0
    while not stop.is_set():
        try:
            await tick()
        except Exception:
            # One bad job must not kill the worker; details go to the log, never job contents.
            log.exception("worker.tick_failed")
        ticks += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    log.info("worker.stopped", ticks=ticks)
    return ticks
