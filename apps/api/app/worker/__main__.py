"""Entry point: `python -m app.worker`."""

import asyncio
import signal
from types import FrameType

from app.config import get_settings
from app.db import dispose_engine
from app.logging import configure_logging
from app.worker import run_worker


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: the event loop has no signal API, fall back to a plain handler.
            def _handler(_signum: int, _frame: FrameType | None) -> None:
                loop.call_soon_threadsafe(stop.set)

            signal.signal(sig, _handler)


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    install_stop_handlers(stop)
    try:
        await run_worker(stop, poll_interval=settings.worker_poll_interval_seconds)
    finally:
        await dispose_engine()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
