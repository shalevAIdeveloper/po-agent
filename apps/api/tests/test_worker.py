import asyncio
import io
import json

from app.logging import configure_logging
from app.worker import run_worker


async def test_runs_ticks_until_stopped() -> None:
    stop = asyncio.Event()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()

    ticks = await run_worker(stop, tick=tick, poll_interval=0.01)

    assert ticks == 3
    assert calls == 3


async def test_stop_before_start_runs_nothing() -> None:
    stop = asyncio.Event()
    stop.set()

    assert await run_worker(stop, poll_interval=0.01) == 0


async def test_failing_tick_does_not_kill_worker() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    stop = asyncio.Event()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("ACME 123 Main St $4.50")
        stop.set()

    ticks = await run_worker(stop, tick=tick, poll_interval=0.01)

    assert ticks == 2
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["event"] for line in lines] == [
        "worker.started",
        "worker.tick_failed",
        "worker.stopped",
    ]
    assert lines[1]["exc_type"] == "builtins.RuntimeError"
    assert "ACME" not in stream.getvalue()


async def test_stop_interrupts_poll_wait() -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, stop.set)

    # A 60s poll interval would hang the test if setting `stop` did not wake the wait.
    ticks = await asyncio.wait_for(run_worker(stop, poll_interval=60), timeout=2)

    assert ticks == 1
