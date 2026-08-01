"""
Profile-photo processing for designer accounts.

Every uploaded photo is validated, center-cropped to a square, resized to a
fixed maximum, and re-encoded as JPEG before it's ever written to disk — so
storage per designer is small and bounded (roughly 80-200KB) regardless of
what was actually uploaded. The original bytes are never kept.
"""
from __future__ import annotations
import io

MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_DIMENSION = 640
JPEG_QUALITY = 82


class UnsupportedPhoto(Exception):
    pass


def process_photo(data: bytes) -> bytes:
    if len(data) > MAX_PHOTO_BYTES:
        raise UnsupportedPhoto("That image is larger than 3MB. Please upload a smaller one.")

    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise UnsupportedPhoto("Couldn't read that file as an image. Please upload a JPG, PNG, or WebP.") from e

    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise UnsupportedPhoto("Please upload a JPG, PNG, or WebP image.")

    img = img.convert("RGB")

    # Center-crop to a square before resizing, so every profile photo has a
    # consistent aspect ratio regardless of what was uploaded.
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    if side > MAX_DIMENSION:
        img = img.resize((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
