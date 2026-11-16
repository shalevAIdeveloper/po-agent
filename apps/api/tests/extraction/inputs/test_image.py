import io
import os
import time

import pytest
from PIL import Image

from app.extraction.inputs import parse_document
from app.extraction.inputs.errors import InputError
from app.extraction.inputs.image import encode_for_model, parse_image
from app.extraction.inputs.limits import MAX_IMAGE_SIDE, MAX_PNG_BYTES, MAX_SOURCE_PIXELS
from app.llm.types import ImagePart
from tests.extraction.inputs.files import EXIF_MARKER, make_image, png_header_only


def opened(part: ImagePart) -> Image.Image:
    image = Image.open(io.BytesIO(part.data))
    image.load()
    return image


def test_png_is_reencoded_as_rgb_png() -> None:
    part = parse_image(make_image((200, 100)))
    assert part.media_type == "image/png"
    image = opened(part)
    assert (image.format, image.mode, image.size) == ("PNG", "RGB", (200, 100))


def test_exif_orientation_is_applied() -> None:
    # Orientation 6 = rotate 90 degrees clockwise to display: 200x100 shows as 100x200.
    part = parse_image(make_image((200, 100), fmt="JPEG", orientation=6))
    assert opened(part).size == (100, 200)


def test_no_metadata_reaches_the_output() -> None:
    sources = [
        make_image(fmt="JPEG", orientation=1),
        make_image(text_chunk="TEXT-MARKER-91c2", icc_profile=b"ICC-MARKER-0a3b" * 8),
    ]
    for source in sources:
        part = parse_image(source)
        for marker in (EXIF_MARKER.encode(), b"TEXT-MARKER-91c2", b"Exif", b"tEXt", b"iCCP"):
            assert marker not in part.data
        info = opened(part).info
        assert not {"exif", "icc_profile", "Comment"} & set(info)


def test_the_long_side_is_scaled_down() -> None:
    image = opened(parse_image(make_image((4000, 1000))))
    assert image.size == (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE // 4)


def test_a_small_image_is_not_scaled_up() -> None:
    assert opened(parse_image(make_image((40, 30)))).size == (40, 30)


def test_transparency_is_flattened_onto_white() -> None:
    part = parse_image(make_image((10, 10), mode="RGBA", color=(0, 0, 0, 0)))
    assert opened(part).getpixel((5, 5)) == (255, 255, 255)


def test_a_large_noisy_page_is_sent_as_jpeg() -> None:
    noise = Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3))
    part = encode_for_model(noise)
    assert part.media_type == "image/jpeg"
    assert len(part.data) < MAX_PNG_BYTES * 2


def test_a_decompression_bomb_is_refused_before_decoding() -> None:
    # 100,000 x 100,000 = 10 gigapixels claimed by a ~100-byte file.
    start = time.perf_counter()
    with pytest.raises(InputError) as caught:
        parse_image(png_header_only(100_000, 100_000))
    assert caught.value.code == "image_too_large"
    assert time.perf_counter() - start < 1


def test_just_over_the_pixel_cap_is_refused() -> None:
    side = int(MAX_SOURCE_PIXELS**0.5) + 1
    with pytest.raises(InputError, match="image_too_large"):
        parse_image(png_header_only(side, side))


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n" + b"garbage" * 10,
        make_image()[:60],  # truncated
        b"\xff\xd8\xff" + b"\x00" * 50,
    ],
)
def test_corrupt_images_are_invalid_files(data: bytes) -> None:
    with pytest.raises(InputError) as caught:
        parse_document(data)
    assert caught.value.code == "invalid_file"
    assert str(caught.value) == "invalid_file"


def test_an_uploaded_image_has_no_text_layer() -> None:
    document = parse_document(make_image(fmt="JPEG"))
    assert (document.kind, document.text, document.page_count) == ("jpeg", None, 1)
    assert len(document.images) == 1
