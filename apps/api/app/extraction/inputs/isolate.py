"""Parse in a child process with a time limit (owner, 2026-11-16; ADR 0012).

PDFium and Pillow's decoders are native code: a thread can't stop them, and a crash in
them would take the worker down. The child (`child.py`) is a fresh interpreter:
- started with `subprocess` and an **empty environment** (only `PATH`, plus `SYSTEMROOT`
  on Windows): no `DATABASE_URL`, `LLM_API_KEY` or any other secret (`sec` M1);
  `python -I -B` ignores `PYTHON*` variables, the user site and the current directory,
  and the app directory is appended to `sys.path`, so it can't shadow the stdlib (L-8);
- runs as the worker's user, so on Linux the worker first makes itself non-dumpable
  (`PR_SET_DUMPABLE` 0): a compromised child can't read the worker's
  `/proc/<pid>/environ` or memory (`sec` delta M-A). Processes that started the worker
  are not covered: the worker must be `exec`ed, with no shell or launcher holding the
  secrets (ADR 0012);
- gets the file on stdin and nothing else (no inherited descriptors, stderr to devnull,
  `sec` L4); runs only the parsers: no logging, no DB. Network access is not blocked
  (Follow-up M1-PARSE-NET);
- on Linux has memory, CPU and no-new-process limits, set before it reads the file;
- runs in its own session (POSIX): the parent kills the whole process group when it is
  done, whatever happened (`sec` delta M-B);
- answers with a JSON header plus raw image bytes (`wire.py`), never a pickle, and the
  parent checks it like any untrusted input (`sec` M2). The parent reads at most
  `MAX_REPLY_BYTES`;
- is killed after the timeout (`parse_timeout`). The parent waits on the process, not on
  its stdout, so a pipe held open can't stall it. No answer, a non-zero exit, too much
  output, an unfinished reply or a malformed one is `parse_failed`.

The call blocks; the worker runs it with `asyncio.to_thread` (PR 14).
"""

import contextlib
import functools
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Final

from app.extraction.inputs import ParsedDocument, Rejection, log_outcome
from app.extraction.inputs.limits import MAX_FILE_BYTES, PARSE_TIMEOUT_SECONDS
from app.extraction.inputs.wire import MAX_REPLY_BYTES, WireError, decode

# The directory that holds the `app` package (`/app` in the image, `apps/api` locally).
APP_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_PARSER: Final = "app.extraction.inputs:run_parsers"
_PARSER_RE: Final = re.compile(r"(?:app|tests)(?:\.[a-z_][a-z0-9_]*)+:[a-z_][a-z0-9_]*")
_BOOTSTRAP: Final = (
    "import sys; sys.path.append(sys.argv[1]); from app.extraction.inputs.child import main; main()"
)
_READ_CHUNK: Final = 64 * 1024
_REAP_SECONDS: Final = 5.0
_POLL_SECONDS: Final = 0.02
_PR_SET_DUMPABLE: Final = 4


def child_environment() -> dict[str, str]:
    if sys.platform == "win32":
        # `os.defpath` on Windows starts with the current directory (`sec` delta L-9).
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        # SYSTEMROOT: the interpreter needs it to start.
        return {"PATH": str(Path(system_root) / "System32"), "SYSTEMROOT": system_root}
    return {"PATH": os.defpath}


@functools.cache
def _make_parent_undumpable() -> None:
    """Linux: no other process of this user may read our environ or memory (M-A)."""
    if sys.platform == "linux":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))


def _kill(process: subprocess.Popen[bytes]) -> None:
    """The child and anything left in its process group."""
    if sys.platform != "win32":
        import signal

        # The group outlives a reaped leader while a member lives, so its id isn't reused
        # before then. ProcessLookupError/PermissionError: nothing left in the group.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):  # already gone
        process.kill()


def _exited(process: subprocess.Popen[bytes]) -> bool:
    """Whether the child exited, without reaping it on POSIX (`sec` delta 2, Low 1)."""
    if sys.platform == "win32":
        return process.poll() is not None  # no process group to kill on Windows
    result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    return result is not None


def _wait_unreaped(process: subprocess.Popen[bytes], timeout: float, stop: threading.Event) -> bool:
    """Until the child exits or `stop` is set: True; False at the timeout."""
    deadline = time.monotonic() + timeout
    while not _exited(process):
        if stop.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        stop.wait(min(_POLL_SECONDS, remaining))
    return True


class _Reader:
    """Owns the child's stdout: reads up to MAX_REPLY_BYTES; more is an overflow."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self.data = bytearray()
        # Set on overflow; the main thread does all the killing (`sec` delta 2, Low 1).
        self.overflow = threading.Event()

    def run(self) -> None:
        try:
            while chunk := self._stream.read(_READ_CHUNK):
                if len(self.data) + len(chunk) > MAX_REPLY_BYTES:
                    self.overflow.set()  # a child blocked on a full pipe never exits
                    return
                self.data.extend(chunk)
        finally:
            self._stream.close()


def _write(stream: IO[bytes], data: bytes) -> None:
    """Owns the child's stdin: writes the file, then closes it (`sec` delta L-5)."""
    try:
        view = memoryview(data)
        while view:
            written = stream.write(view)  # unbuffered: may write part
            if not written:
                break  # never spin (a blocking pipe doesn't return 0 or None)
            view = view[written:]
    except (OSError, ValueError):
        pass  # the child exited early (or was killed): its reply says why
    finally:
        with contextlib.suppress(OSError):
            stream.close()


def _run_child(data: bytes, timeout: float, parser: str) -> ParsedDocument | Rejection:
    if _PARSER_RE.fullmatch(parser) is None:
        raise ValueError("parser must be app.* or tests.* 'module:function'")
    _make_parent_undumpable()
    process = subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter and module
        [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, str(APP_ROOT), parser],
        bufsize=0,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_environment(),
        cwd=APP_ROOT,
        close_fds=True,
        start_new_session=sys.platform != "win32",
    )
    assert process.stdin is not None and process.stdout is not None  # noqa: S101 - PIPE set
    reader = _Reader(process.stdout)
    # Each thread owns and closes its stream: the parent never closes a stream another
    # thread may be blocked on.
    writer_thread = threading.Thread(target=_write, args=(process.stdin, data), daemon=True)
    reader_thread = threading.Thread(target=reader.run, daemon=True)
    writer_thread.start()
    reader_thread.start()
    timed_out = False
    exit_code: int | None = None
    try:
        timed_out = not _wait_unreaped(process, timeout, reader.overflow)
    finally:
        # Whatever happened: nothing the child started may keep running or keep the
        # pipes open. Killing the group closes their ends, so both threads finish. The
        # child isn't reaped yet, so its pid (the group id) can't have been reused.
        _kill(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            exit_code = process.wait(_REAP_SECONDS)
        reader_thread.join(_REAP_SECONDS)
        writer_thread.join(_REAP_SECONDS)
    if timed_out:
        return Rejection(code="parse_timeout")
    if reader.overflow.is_set() or exit_code != 0 or reader_thread.is_alive():
        # Still reading: something outside the group holds the pipe; the reply is unknown.
        return Rejection(code="parse_failed")
    try:
        return decode(bytes(reader.data))
    except (WireError, TypeError, ValueError, KeyError):
        # Defence in depth: `decode` should raise only WireError (`sec` delta L-1).
        return Rejection(code="parse_failed")


def parse_isolated(data: bytes, *, timeout: float = PARSE_TIMEOUT_SECONDS) -> ParsedDocument:
    """Like `parse_document`, in a child process. Raises `InputError` with a code."""
    return _parse_isolated(data, timeout, DEFAULT_PARSER)


def _parse_isolated(data: bytes, timeout: float, parser: str) -> ParsedDocument:
    """`parser` ("module:function", `app.*` or `tests.*`) is for tests (`sec` delta L-8)."""
    if len(data) > MAX_FILE_BYTES:
        outcome: ParsedDocument | Rejection = Rejection(code="file_too_large")
    else:
        outcome = _run_child(data, timeout, parser)
    return log_outcome(outcome, len(data))
