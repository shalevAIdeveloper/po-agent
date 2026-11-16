"""Legacy Office containers (CFB / OLE2): only the names of the streams, read with bounds.

Excel 97-2003 (`.xls`) keeps its data in a `Workbook` (or `Book`) stream; a
password-protected `.xlsx` is a CFB container with an `EncryptedPackage` stream. Telling
them apart lets the user see a useful message (owner, 2026-11-16). Nothing else is read:
the directory chain is followed through the FAT sectors listed in the header only, at
most MAX_CFB_DIRECTORY_SECTORS sectors, with loop detection. Anything unexpected gives
no names, and the file stays `unsupported_type`.
"""

import struct
from typing import Final

from app.extraction.inputs.limits import MAX_CFB_DIRECTORY_SECTORS

MAGIC: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_HEADER_SIZE: Final = 512
_SECTOR_SHIFTS: Final = frozenset({9, 12})
_HEADER_DIFAT_ENTRIES: Final = 109
_DIFAT_OFFSET: Final = 0x4C
_DIR_ENTRY_SIZE: Final = 128
_MAX_SECTOR: Final = 0xFFFFFFFA  # higher values are chain markers (end, free, ...)
_STREAM_OR_STORAGE: Final = frozenset({1, 2})
_U32: Final = struct.Struct("<I")


def _u32(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return int(_U32.unpack_from(data, offset)[0])


def stream_names(data: bytes) -> frozenset[str]:
    """Names of the streams and storages in the CFB directory; empty if unreadable."""
    if not data.startswith(MAGIC) or len(data) < _HEADER_SIZE:
        return frozenset()
    shift = int(struct.unpack_from("<H", data, 0x1E)[0])
    if shift not in _SECTOR_SHIFTS:
        return frozenset()
    size = 1 << shift
    per_fat_sector = size // 4

    def sector_offset(sector: int) -> int:
        return (sector + 1) * size

    def next_sector(sector: int) -> int | None:
        fat_index = sector // per_fat_sector
        if fat_index >= _HEADER_DIFAT_ENTRIES:
            return None
        fat_sector = _u32(data, _DIFAT_OFFSET + 4 * fat_index)
        if fat_sector is None or fat_sector > _MAX_SECTOR:
            return None
        return _u32(data, sector_offset(fat_sector) + 4 * (sector % per_fat_sector))

    names: set[str] = set()
    sector = _u32(data, 0x30)
    seen: set[int] = set()
    while sector is not None and sector <= _MAX_SECTOR and sector not in seen:
        if len(seen) >= MAX_CFB_DIRECTORY_SECTORS:
            break
        seen.add(sector)
        start = sector_offset(sector)
        if start + size > len(data):
            break
        for entry in range(start, start + size, _DIR_ENTRY_SIZE):
            name_length = struct.unpack_from("<H", data, entry + 0x40)[0]
            kind = data[entry + 0x42]
            if kind in _STREAM_OR_STORAGE and 2 <= name_length <= 64 and name_length % 2 == 0:
                raw = data[entry : entry + name_length - 2]
                names.add(raw.decode("utf-16-le", errors="replace"))
        sector = next_sector(sector)
    return frozenset(names)
