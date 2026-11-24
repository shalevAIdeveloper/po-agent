"""Scans (M1 PR 6 T5): no text layer, small, deterministic, and faithful to the spec."""

import io

import pytest
from PIL import Image

from app.extraction.inputs import ParsedDocument, run_parsers
from evals.generate import files, meta, render_scan
from evals.generate.cases import CASES
from evals.generate.spec import Case, ScanDoc
from evals.run import FIXTURES_DIR

MAX_RASTER_BYTES = 600 * 1024  # the plan's cap: rasters stay around 400 KB
SCANS = [pytest.param(c, id=c.name) for c in CASES if isinstance(c.document, ScanDoc)]


def _source(case: Case) -> bytes:
    return (FIXTURES_DIR / case.name / case.source_name).read_bytes()


def test_there_are_seven_scans_in_every_output_format() -> None:
    outputs = sorted(c.input_type for c in CASES if isinstance(c.document, ScanDoc))
    assert outputs == ["jpg", "jpg", "pdf", "pdf", "pdf", "png", "png"]


@pytest.mark.parametrize("case", SCANS)
def test_a_scan_is_under_the_raster_cap(case: Case) -> None:
    assert len(_source(case)) <= MAX_RASTER_BYTES


@pytest.mark.parametrize("case", SCANS)
def test_a_scan_has_no_text_layer(case: Case) -> None:
    parsed = run_parsers(_source(case))
    assert isinstance(parsed, ParsedDocument)
    assert parsed.text is None
    assert parsed.images


PDF_SCANS = [
    pytest.param(c, id=c.name)
    for c in CASES
    if isinstance(c.document, ScanDoc) and c.document.output == "pdf"
]


@pytest.mark.parametrize("case", PDF_SCANS)
def test_a_scanned_pdf_embeds_one_jpeg_per_page_without_ascii85(case: Case) -> None:
    data = _source(case)
    pages = meta.load((FIXTURES_DIR / case.name / files.META_NAME).read_bytes()).pages
    assert data.count(b"/DCTDecode") == pages
    assert b"/ASCII85Decode" not in data


@pytest.mark.parametrize("case", SCANS)
def test_a_scan_renders_the_same_twice(case: Case) -> None:
    first = render_scan.render(case, case.document)  # type: ignore[arg-type]
    second = render_scan.render(case, case.document)  # type: ignore[arg-type]
    assert first.source == second.source


def test_the_fax_is_one_bit_and_the_photos_are_rgb() -> None:
    by_name = {c.name: c for c in CASES}
    modes = {
        name: Image.open(io.BytesIO(_source(by_name[name]))).mode
        for name in ("scan_png_fax_bilevel", "scan_png_screenshot", "scan_jpg_phone_photo")
    }
    assert modes == {
        "scan_png_fax_bilevel": "1",
        "scan_png_screenshot": "RGB",
        "scan_jpg_phone_photo": "RGB",
    }


def test_specks_never_touch_ink() -> None:
    import random

    clean = Image.new("L", (200, 100), 255)
    clean.paste(0, (90, 40, 110, 60))  # a "character"
    image = clean.copy()
    render_scan._specks(random.Random(1), image, clean, 2000)

    # A speck keeps at least `margin` (4 px here) of white between itself and any ink.
    near = (86, 36, 114, 64)
    assert image.crop(near).tobytes() == clean.crop(near).tobytes()
    assert image.tobytes() != clean.tobytes()
