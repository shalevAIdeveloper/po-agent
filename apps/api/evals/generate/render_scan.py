"""Scans and photos: a case's PDF rasterized by pypdfium2, then degraded with Pillow.

Deterministic by construction: the only randomness is `random.Random(case.seed)` (grain
tiles and specks are bytes from it, never Pillow's or NumPy's generators), and every
Pillow step is integer or fixed-parameter (rotate, blur, resize, ImageChops). What can
still differ between platforms is the codecs' and PDFium's own output, which is why
committed rasters are compared by decoded pixels (owner decision Q2).

Outputs:
- `pdf`: each page as a grayscale JPEG, embedded unchanged (DCT passthrough, no ASCII85)
  in a reportlab PDF with no text layer;
- `png`: one page (a screenshot in RGB, or a 1-bit fax);
- `jpg`: one page, RGB (a phone photo, a washed-out copy with a stamp).
"""

import io
import random
from typing import Final

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from reportlab import rl_config
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from evals.generate import GENERATOR, render_pdf
from evals.generate.render import Rendered
from evals.generate.spec import Case, ScanDoc

POINTS_PER_INCH: Final = 72
TILE: Final = 256  # the grain tile's side, in pixels
STAMP_RED: Final = (196, 30, 36)


def rasterize(data: bytes, dpi: int) -> list[Image.Image]:
    """Every page, grayscale, at `dpi`."""
    document = pdfium.PdfDocument(data)
    try:
        pages = []
        for page in document:
            bitmap = page.render(scale=dpi / POINTS_PER_INCH, grayscale=True)
            pages.append(bitmap.to_pil().convert("L"))
            page.close()
        return pages
    finally:
        document.close()


def _stamp(size: tuple[float, float], text: str, dpi: int) -> Image.Image:
    """A red rubber stamp, drawn by reportlab and rasterized like the page (no font files)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=size, invariant=True, pageCompression=0)
    pdf.translate(size[0] * 0.58, size[1] * 0.52)
    pdf.rotate(14)
    pdf.setStrokeColorRGB(*(c / 255 for c in STAMP_RED))
    pdf.setFillColorRGB(*(c / 255 for c in STAMP_RED))
    pdf.setLineWidth(3)
    pdf.roundRect(-120, -26, 240, 52, 8, stroke=1, fill=0)
    pdf.setFont(render_pdf.BOLD, 20)
    pdf.drawCentredString(0, -7, text)
    pdf.showPage()
    pdf.save()
    document = pdfium.PdfDocument(buffer.getvalue())
    try:
        page = document[0]
        image: Image.Image = page.render(scale=dpi / POINTS_PER_INCH).to_pil().convert("RGB")
        page.close()
        return image
    finally:
        document.close()


def _grain(rng: random.Random, size: tuple[int, int], sigma: float) -> Image.Image:
    """Signed grain around 128, from a tile of the case's random bytes, repeated."""
    tile = Image.frombytes("L", (TILE, TILE), rng.randbytes(TILE * TILE))
    # Uniform bytes -> spread about 128 with the requested strength (std of U(0,255) ~ 74).
    scale = sigma / 74.0
    tile = tile.point([max(0, min(255, round(128 + (v - 128) * scale))) for v in range(256)])
    grain = Image.new("L", size, 128)
    for x in range(0, size[0], TILE):
        for y in range(0, size[1], TILE):
            grain.paste(tile, (x, y))
    return grain


def _specks(rng: random.Random, image: Image.Image, clean: Image.Image, count: int) -> None:
    """Dust specks, only where the clean page is blank: a speck must never change what a
    character reads as ("JC" into "JG", "4.10" into "4,10"), or the truth would no longer
    be what the image shows."""
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin = max(4, width // 200)
    for _ in range(count):
        x, y, r = rng.randrange(width), rng.randrange(height), rng.choice((1, 1, 2))
        box = (x - r - margin, y - r - margin, x + r + margin + 1, y + r + margin + 1)
        lowest = clean.crop(box).getextrema()[0]
        if not isinstance(lowest, int) or lowest < 230:
            continue
        draw.ellipse((x - r, y - r, x + r, y + r), fill=rng.choice((0, 40, 90)))


def _contrast(image: Image.Image, factor: float) -> Image.Image:
    """Washed out: dark toward a gray, white toward a light gray."""
    low = round(255 - 255 * factor)
    return image.point([round(low + v * factor * 0.92) for v in range(256)])


def degrade(
    page: Image.Image, doc: ScanDoc, rng: random.Random, size: tuple[float, float]
) -> Image.Image:
    image = page
    if doc.contrast != 1.0:
        image = _contrast(image, doc.contrast)
    if doc.blur:
        image = image.filter(ImageFilter.GaussianBlur(doc.blur))
    if doc.noise:
        clean = image
        image = ImageChops.add(image, _grain(rng, image.size, doc.noise), scale=1.0, offset=-128)
        _specks(rng, image, clean, 120)
    if doc.rotate:
        image = image.rotate(doc.rotate, resample=Image.Resampling.BICUBIC, fillcolor=255)
    if doc.bilevel:
        # A fax: half the vertical resolution, then 1 bit per pixel, no dithering.
        width, height = image.size
        image = image.resize((width, height // 2), Image.Resampling.BOX)
        image = image.resize((width, height), Image.Resampling.NEAREST)
        return image.point(lambda v: 255 if v > 150 else 0).convert("1", dither=Image.Dither.NONE)
    rgb = image.convert("RGB")
    if doc.stamp:
        stamp = _stamp(size, doc.stamp, doc.dpi).resize(rgb.size)
        rgb = ImageChops.multiply(rgb, stamp)
    if doc.photo:
        rgb = _photo(rgb)
    return rgb


def _photo(page: Image.Image) -> Image.Image:
    """A phone photo of the sheet: warm, unevenly lit, on a desk, a little off-center."""
    width, height = page.size
    # A vignette: bright in the middle, darker toward the corners (no rotation, so no
    # empty corners).
    light = Image.radial_gradient("L").resize((width, height), Image.Resampling.BILINEAR)
    light = light.point([round(255 - v * 0.22) for v in range(256)]).convert("RGB")
    tint = Image.new("RGB", (width, height), (255, 246, 228))
    sheet = ImageChops.multiply(ImageChops.multiply(page, light), tint)
    border = round(width * 0.06)
    desk = Image.new("RGB", (width + 2 * border, height + 2 * border), (92, 78, 64))
    desk.paste(sheet, (border + border // 3, border - border // 4))
    return desk


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=False, progressive=False)
    return buffer.getvalue()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def _scan_pdf(pages: list[bytes], size: tuple[float, float], name: str) -> bytes:
    buffer = io.BytesIO()
    previous = rl_config.useA85
    rl_config.useA85 = 0  # the JPEG bytes go in as they are (DCT passthrough)
    try:
        pdf = canvas.Canvas(buffer, pagesize=size, invariant=True, pageCompression=0)
        pdf.setAuthor(GENERATOR)
        pdf.setCreator(GENERATOR)
        pdf.setTitle(f"eval case {name}")
        pdf.setSubject("synthetic eval fixture")
        for jpeg in pages:
            pdf.drawImage(ImageReader(io.BytesIO(jpeg)), 0, 0, width=size[0], height=size[1])
            pdf.showPage()
        pdf.save()
    finally:
        rl_config.useA85 = previous
    return buffer.getvalue()


def render(case: Case, doc: ScanDoc) -> Rendered:
    base = render_pdf.render(case, doc.pdf)
    size = landscape(letter) if doc.pdf.layout == "landscape" else letter
    rng = random.Random(case.seed)
    pages = [degrade(page, doc, rng, size) for page in rasterize(base.data, doc.dpi)]
    if doc.output == "pdf":
        jpegs = [_jpeg(page.convert("L"), doc.jpeg_quality) for page in pages]
        return Rendered(_scan_pdf(jpegs, size, case.name), base.pages, base.line_pages)
    if len(pages) != 1:
        raise ValueError(f"{case.name}: an image holds one page")
    data = _png(pages[0]) if doc.output == "png" else _jpeg(pages[0], doc.jpeg_quality)
    return Rendered(data, 1, (1,) * len(case.lines))
