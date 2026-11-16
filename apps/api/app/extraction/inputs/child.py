"""The parsing child process (started by `isolate.py`; never imported by the worker itself).

Reads the file from stdin, runs the parsers, writes one `wire` reply to stdout, exits 0.
Any failure exits non-zero with nothing on stdout; stderr is /dev/null in the parent.
The limits are set before the file is read. On Linux:
- RLIMIT_AS: CHILD_MEMORY_BYTES of address space;
- RLIMIT_NPROC 0: it can't start a process (or a thread), so nothing it starts can hold
  the reply pipe open or outlive the kill (`sec` delta M-B);
- RLIMIT_CPU: a backstop past the parent's timeout.
Each is a hard limit too, so code running in the child can't raise it again.
"""

import importlib
import os
import sys
from typing import Final

FAILED_EXIT: Final = 70


def _limit() -> None:
    if sys.platform == "linux":
        import resource

        from app.extraction.inputs.limits import CHILD_MEMORY_BYTES, PARSE_TIMEOUT_SECONDS

        cpu_seconds = int(PARSE_TIMEOUT_SECONDS) + 15
        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def main() -> None:
    try:
        _limit()
        module_name, _, attribute = sys.argv[2].partition(":")
        parser = getattr(importlib.import_module(module_name), attribute)
        from app.extraction.inputs.wire import encode

        data = sys.stdin.buffer.read()
        reply = encode(parser(data))
        sys.stdout.buffer.write(reply)
        sys.stdout.buffer.flush()
    except BaseException:
        os._exit(FAILED_EXIT)
    os._exit(0)
