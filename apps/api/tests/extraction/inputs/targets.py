"""Parsers for `parse_isolated` tests. Module-level, so a spawned child can import them."""

import os
import time

from app.extraction.inputs import ParsedDocument, Rejection


def hang(_data: bytes) -> ParsedDocument | Rejection:
    time.sleep(3600)
    raise AssertionError("unreachable")


def die(_data: bytes) -> ParsedDocument | Rejection:
    os._exit(3)  # like a native crash: no answer, no traceback


def raise_secret(_data: bytes) -> ParsedDocument | Rejection:
    raise RuntimeError("SECRET-IN-TRACEBACK-41d0")


def allocate(_data: bytes) -> ParsedDocument | Rejection:
    blocks = [bytearray(256 * 1024 * 1024) for _ in range(16)]  # 4 GiB
    raise AssertionError(f"allocated {len(blocks)} blocks")


def wrong_answer(_data: bytes) -> ParsedDocument | Rejection:
    return "not a document"  # type: ignore[return-value]
