"""Parse in a child process with a time limit (owner, 2026-11-16; ADR 0012).

PDFium and Pillow's decoders are native code: a thread can't stop them, and a crash in
them would take the worker down. The child (`child.py`) is a fresh interpreter:
- started with `subprocess` and an **empty environment** (only `PATH`, plus `SYSTEMROOT`
  on Windows): no `DATABASE_URL`, `LLM_API_KEY` or any other secret (`sec` M1);
  `python -I -B` ignores `PYTHON*` variables, the user site and the current directory;
- gets the file on stdin and nothing else (no inherited descriptors, stderr to devnull,
  `sec` L4); runs only the parsers: no logging, no network, no DB;
- on Linux has an address-space limit (CHILD_MEMORY_BYTES), set before it reads the file;
- answers with a JSON header plus raw image bytes (`wire.py`), never a pickle, and the
  parent checks it like any untrusted input (`sec` M2). The parent reads at most
  `MAX_REPLY_BYTES`;
- is killed after the timeout (`parse_timeout`). No answer, a non-zero exit, too much
  output or a malformed reply is `parse_failed`.

The call blocks; the worker runs it with `asyncio.to_thread` (PR 14).
"""

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Final

from app.extraction.inputs import ParsedDocument, Rejection, log_outcome
from app.extraction.inputs.limits import MAX_FILE_BYTES, PARSE_TIMEOUT_SECONDS
from app.extraction.inputs.wire import MAX_REPLY_BYTES, WireError, decode

# The directory that holds the `app` package (`/app` in the image, `apps/api` locally).
APP_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_PARSER: Final = "app.extraction.inputs:run_parsers"
_BOOTSTRAP: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from app.extraction.inputs.child import main; main()"
)
_READ_CHUNK: Final = 64 * 1024
_REAP_SECONDS: Final = 5.0


def child_environment() -> dict[str, str]:
    env = {"PATH": os.defpath}
    if sys.platform == "win32" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]  # the interpreter needs it to start
    return env


class _Reader:
    """Reads the child's stdout up to MAX_REPLY_BYTES; more is an overflow."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self.data = bytearray()
        self.overflow = False

    def run(self) -> None:
        while chunk := self._stream.read(_READ_CHUNK):
            if len(self.data) + len(chunk) > MAX_REPLY_BYTES:
                self.overflow = True
                return
            self.data.extend(chunk)


def _write(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
        stream.close()
    except OSError:
        pass  # the child exited early (or was killed): its reply says why


def _run_child(data: bytes, timeout: float, parser: str) -> ParsedDocument | Rejection:
    process = subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter and module
        [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, str(APP_ROOT), parser],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_environment(),
        cwd=APP_ROOT,
        close_fds=True,
    )
    assert process.stdin is not None and process.stdout is not None  # noqa: S101 - PIPE set
    reader = _Reader(process.stdout)
    threads = [
        threading.Thread(target=_write, args=(process.stdin, data), daemon=True),
        threading.Thread(target=reader.run, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        threads[1].join(timeout)
        timed_out = threads[1].is_alive()
        if timed_out or reader.overflow:
            process.kill()
        try:
            exit_code = process.wait(_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            exit_code = None  # closed its stdout but kept running: killed below
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(_REAP_SECONDS)
        for thread in threads:
            thread.join(_REAP_SECONDS)
        process.stdout.close()
    if timed_out:
        return Rejection(code="parse_timeout")
    if reader.overflow or exit_code != 0:
        return Rejection(code="parse_failed")
    try:
        return decode(bytes(reader.data))
    except WireError:
        return Rejection(code="parse_failed")


def parse_isolated(
    data: bytes,
    *,
    timeout: float = PARSE_TIMEOUT_SECONDS,
    parser: str = DEFAULT_PARSER,
) -> ParsedDocument:
    """Like `parse_document`, in a child process. Raises `InputError` with a code.

    `parser` ("module:function") exists for tests; the worker never passes it.
    """
    if len(data) > MAX_FILE_BYTES:
        outcome: ParsedDocument | Rejection = Rejection(code="file_too_large")
    else:
        outcome = _run_child(data, timeout, parser)
    return log_outcome(outcome, len(data))
