"""Parsers for `parse_isolated` tests, named as "tests.extraction.inputs.targets:<name>".

They run inside the child process (a fresh interpreter with an empty environment).
"""

import os
import sys
import time

from app.extraction.inputs import ParsedDocument, Rejection
from app.extraction.inputs.wire import MAX_REPLY_BYTES

MODULE = "tests.extraction.inputs.targets"


def name(function: str) -> str:
    return f"{MODULE}:{function}"


def hang(_data: bytes) -> ParsedDocument | Rejection:
    time.sleep(3600)
    raise AssertionError("unreachable")


def die(_data: bytes) -> ParsedDocument | Rejection:
    os._exit(3)  # like a native crash: no answer


def raise_secret(_data: bytes) -> ParsedDocument | Rejection:
    raise RuntimeError("SECRET-IN-TRACEBACK-41d0")


def allocate(_data: bytes) -> ParsedDocument | Rejection:
    blocks = [bytearray(256 * 1024 * 1024) for _ in range(16)]  # 4 GiB
    raise AssertionError(f"allocated {len(blocks)} blocks")


def wrong_answer(_data: bytes) -> ParsedDocument | Rejection:
    return "not a document"  # type: ignore[return-value]


def environment(_data: bytes) -> ParsedDocument | Rejection:
    """Reports the child's environment variable names as the document text."""
    return ParsedDocument(
        kind="eml", text=" ".join(sorted(os.environ)) or "-", images=(), page_count=1, warnings=()
    )


def echo_size(data: bytes) -> ParsedDocument | Rejection:
    return ParsedDocument(kind="eml", text=str(len(data)), images=(), page_count=1, warnings=())


def junk_reply(_data: bytes) -> ParsedDocument | Rejection:
    """A compromised child: writes bytes that aren't a reply, exits 0."""
    sys.stdout.buffer.write(b"\x80\x04\x95cos\nsystem\n(S'echo pwned'\ntR.")
    sys.stdout.buffer.flush()
    os._exit(0)


def flood(_data: bytes) -> ParsedDocument | Rejection:
    """A compromised child: writes more than the parent will ever read."""
    chunk = b"\0" * (1024 * 1024)
    for _ in range(MAX_REPLY_BYTES // len(chunk) + 2):
        sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    os._exit(0)


def spawn_and_exit(_data: bytes) -> ParsedDocument | Rejection:
    """Starts a grandchild that holds the reply pipe open, answers, exits (`sec` M-B).

    On Linux (not root) the child can't start a process: this raises, `parse_failed`.
    """
    import subprocess

    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout.fileno(),
        stderr=subprocess.DEVNULL,
    )
    return ParsedDocument(
        kind="eml", text=f"grandchild {grandchild.pid}", images=(), page_count=1, warnings=()
    )


def read_parent_environ(_data: bytes) -> ParsedDocument | Rejection:
    """Reports whether the parent's /proc environ (its secrets) is readable (`sec` M-A)."""
    try:
        with open(f"/proc/{os.getppid()}/environ", "rb") as environ:
            environ.read()
        verdict = "readable"
    except PermissionError:
        verdict = "denied"
    return ParsedDocument(kind="eml", text=verdict, images=(), page_count=1, warnings=())


def close_and_linger(_data: bytes) -> ParsedDocument | Rejection:
    """Closes stdout without an answer and keeps running."""
    sys.stdout.close()
    os.close(1)
    time.sleep(3600)
    raise AssertionError("unreachable")
