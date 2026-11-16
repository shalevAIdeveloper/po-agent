"""Parse in a child process with a time limit (owner, 2026-11-16; ADR 0012).

PDFium and Pillow's decoders are native code: a thread can't stop them, and a crash in
them would take the worker down. The child:
- is started with `spawn` (no copy of the worker's event loop, DB pool or secrets in
  memory; it gets only the file bytes),
- runs `run_parsers` only: no logging, no network, no DB,
- on Linux has an address-space limit (CHILD_MEMORY_BYTES), so a bomb that slips past the
  pixel and page caps fails with `parse_failed` instead of exhausting the container,
- is killed after the timeout (`parse_timeout`). A child that dies without an answer is
  `parse_failed`.

The call blocks; the worker runs it with `asyncio.to_thread` (PR 14).
"""

import multiprocessing
import os
import sys
from collections.abc import Callable
from typing import Any, Final, Protocol

from app.extraction.inputs import ParsedDocument, Rejection, log_outcome, run_parsers
from app.extraction.inputs.limits import CHILD_MEMORY_BYTES, PARSE_TIMEOUT_SECONDS

Parser = Callable[[bytes], ParsedDocument | Rejection]


class _Channel(Protocol):
    """One end of a `Pipe` (`Connection` on Linux, `PipeConnection` on Windows)."""

    def poll(self, timeout: float | None = ...) -> bool: ...
    def recv(self) -> Any: ...
    def send(self, obj: Any) -> None: ...
    def close(self) -> None: ...


_EXIT_GRACE_SECONDS: Final = 1.0
# After a kill, how long to wait for the process to be reaped.
_REAP_SECONDS: Final = 5.0
_CHILD_FAILED_EXIT: Final = 70


def _limit_memory() -> None:
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))


def _child(connection: _Channel, data: bytes, parser: Parser) -> None:
    try:
        _limit_memory()
        outcome = parser(data)
        connection.send(outcome)
        connection.close()
    except BaseException:
        # No traceback: it could quote the file. The parent sees no answer: parse_failed.
        os._exit(_CHILD_FAILED_EXIT)


def _receive(connection: _Channel, timeout: float) -> ParsedDocument | Rejection:
    if not connection.poll(timeout):
        return Rejection(code="parse_timeout")
    try:
        outcome = connection.recv()
    except (EOFError, OSError):
        return Rejection(code="parse_failed")
    if isinstance(outcome, ParsedDocument | Rejection):
        return outcome
    return Rejection(code="parse_failed")


def parse_isolated(
    data: bytes,
    *,
    timeout: float = PARSE_TIMEOUT_SECONDS,
    parser: Parser = run_parsers,
) -> ParsedDocument:
    """Like `parse_document`, in a child process. Raises `InputError` with a code."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(sender, data, parser), daemon=True)
    process.start()
    sender.close()  # only the child holds the sending end: its exit gives us EOF
    try:
        outcome = _receive(receiver, timeout)
    finally:
        receiver.close()
        process.join(_EXIT_GRACE_SECONDS)  # a child that answered is exiting on its own
        if process.is_alive():
            process.kill()
            process.join(_REAP_SECONDS)
    return log_outcome(outcome, len(data))
