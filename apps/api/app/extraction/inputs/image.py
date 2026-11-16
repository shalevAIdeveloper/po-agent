"""PNG / JPEG uploads, and the encoder every image sent to the model goes through.

- Only Pillow's PNG and JPEG decoders are used (`formats=`), whatever else Pillow could open.
- The pixel count is checked from the header, before any decoding (decompression bombs);
  Pillow's own bomb warning is an error too.
- Only the first frame is used (an animated PNG's default image, the main picture of a
  phone's multi-picture JPEG).
- EXIF orientation is applied, transparency is flattened onto white, the long side is
  scaled down to MAX_IMAGE_SIDE, and the image is encoded anew: no EXIF (GPS), ICC
  profile or text chunks reach the model or storage.
"""

import io
import warnings
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from app.extraction.inputs.errors import InputError
from app.extraction.inputs.limits import (
    JPEG_QUALITY,
    MAX_IMAGE_SIDE,
    MAX_PNG_BYTES,
    MAX_SOURCE_PIXELS,
)
from app.llm.types import ImagePart

# Pillow raises DecompressionBombError above twice this, and warns above it. We check the
# size ourselves first; this is the backstop for any other path into the decoder.
Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS

_DECODERS: Final = ("PNG", "JPEG", "MPO")  # MPO = a JPEG with extra frames (phones)
_WHITE: Final = (255, 255, 255)


def encode_for_model(image: Image.Image) -> ImagePart:
    """RGB, long side <= MAX_IMAGE_SIDE, PNG (sharp text) or JPEG if the PNG is big."""
    if image.mode != "RGB":
        if "A" in image.getbands() or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, _WHITE)
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
    if max(image.size) > MAX_IMAGE_SIDE:
        image = ImageOps.contain(image, (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", image.size)
    clean.paste(image)  # a new image: nothing of the source's `info` (EXIF, ICC, text) left
    buffer = io.BytesIO()
    clean.save(buffer, format="PNG", optimize=True)
    if buffer.tell() <= MAX_PNG_BYTES:
        return ImagePart(data=buffer.getvalue(), media_type="image/png")
    buffer = io.BytesIO()
    clean.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return ImagePart(data=buffer.getvalue(), media_type="image/jpeg")


def parse_image(data: bytes) -> ImagePart:
    """Other Pillow exceptions (corrupt data) become `invalid_file` in `parse_document`."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=_DECODERS) as source:
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise InputError("invalid_file")
                if width * height > MAX_SOURCE_PIXELS:
                    raise InputError("image_too_large")
                source.seek(0)
                if source.format in ("JPEG", "MPO"):
                    # Decode a big photo at a reduced scale (still >= 2x the size we send).
                    source.draft("RGB", (2 * MAX_IMAGE_SIDE, 2 * MAX_IMAGE_SIDE))
                source.load()
                return encode_for_model(ImageOps.exif_transpose(source))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InputError("image_too_large") from None
    except UnidentifiedImageError:
        raise InputError("invalid_file") from None
