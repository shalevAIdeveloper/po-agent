"""The parsing child process (started by `isolate.py`; never imported by the worker itself).

Reads the file from stdin, runs the parsers, writes one `wire` reply to stdout, exits 0.
Any failure exits non-zero with nothing on stdout; stderr is /dev/null in the parent.
The memory limit is set before the file is read.
"""

import importlib
import os
import sys
from typing import Final

FAILED_EXIT: Final = 70


def _limit_memory() -> None:
    if sys.platform == "linux":
        import resource

        from app.extraction.inputs.limits import CHILD_MEMORY_BYTES

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))


def main() -> None:
    try:
        _limit_memory()
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
