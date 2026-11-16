"""Tiny synthetic files built in the tests: nothing binary is committed (M1 plan, PR 4)."""

import io
import struct
import zlib
from email.message import EmailMessage

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from reportlab.lib import pdfencrypt
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

LETTER = (612.0, 792.0)
EXIF_MARKER = "EXIF-MARKER-7d1f"


def make_pdf(
    pages: list[str],
    *,
    encrypt: pdfencrypt.StandardEncryption | None = None,
    pagesize: tuple[float, float] = LETTER,
) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=pagesize, encrypt=encrypt, invariant=True)
    for text in pages:
        for number, line in enumerate(text.split("\n")):
            pdf.drawString(72, pagesize[1] - 72 - 14 * number, line)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_scanned_pdf() -> bytes:
    """One page that is only a picture (no text layer)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER, invariant=True)
    picture = Image.new("RGB", (300, 100), (255, 255, 255))
    picture.paste((0, 0, 0), (20, 40, 280, 60))
    pdf.drawImage(ImageReader(picture), 72, 600, width=300, height=100)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_image(
    size: tuple[int, int] = (200, 100),
    *,
    fmt: str = "PNG",
    mode: str = "RGB",
    color: tuple[int, ...] = (200, 30, 30),
    orientation: int | None = None,
    text_chunk: str | None = None,
    icc_profile: bytes | None = None,
) -> bytes:
    image = Image.new(mode, size, color)
    kwargs: dict[str, object] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        exif[0x010E] = EXIF_MARKER  # ImageDescription: must never reach the output
        kwargs["exif"] = exif.tobytes()
    if text_chunk is not None:
        info = PngInfo()
        info.add_text("Comment", text_chunk)
        kwargs["pnginfo"] = info
    if icc_profile is not None:
        kwargs["icc_profile"] = icc_profile
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def png_header_only(width: int, height: int) -> bytes:
    """A PNG whose header claims `width` x `height`, with almost no pixel data."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 16)
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    )


def make_email(
    *,
    plain: str | None = "PO 4500012345\n1 x Widget 00123-A",
    html: str | None = None,
    sender: str = "Buyer <buyer@acme.example>",
    date: str | None = "Mon, 16 Nov 2026 09:30:00 -0500",
    attachment: bytes | None = None,
    forwarded: EmailMessage | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "orders@distributor.example"
    message["Subject"] = "Purchase order"
    if date is not None:
        message["Date"] = date
    if plain is not None:
        message.set_content(plain)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    if attachment is not None:
        message.add_attachment(
            attachment, maintype="application", subtype="pdf", filename="po-secret-name.pdf"
        )
    if forwarded is not None:
        message.add_attachment(forwarded)
    return bytes(message)
