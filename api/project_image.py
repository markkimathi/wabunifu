"""Featured-project cover-image processing for designer accounts.

Mirrors photo.py's shape exactly (validate -> crop -> resize -> re-encode
JPEG, original bytes never kept) but crops to a 4:3 landscape instead of a
square — a project cover reads as a landscape thumbnail (a screenshot, a
mockup), not a face.
"""
from __future__ import annotations
import io

MAX_IMAGE_BYTES = 4 * 1024 * 1024
TARGET_RATIO = 4 / 3  # width / height
MAX_WIDTH = 800
MAX_HEIGHT = int(MAX_WIDTH / TARGET_RATIO)
JPEG_QUALITY = 82


class UnsupportedImage(Exception):
    pass


def process_project_image(data: bytes) -> bytes:
    if len(data) > MAX_IMAGE_BYTES:
        raise UnsupportedImage("That image is larger than 4MB. Please upload a smaller one.")

    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise UnsupportedImage("Couldn't read that file as an image. Please upload a JPG, PNG, or WebP.") from e

    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise UnsupportedImage("Please upload a JPG, PNG, or WebP image.")

    img = img.convert("RGB")

    # Center-crop to a 4:3 landscape before resizing, so every project
    # cover has a consistent aspect ratio regardless of what was uploaded.
    w, h = img.size
    if w / h > TARGET_RATIO:
        # Wider than 4:3 — crop width down, keep full height.
        target_w = round(h * TARGET_RATIO)
        left = (w - target_w) // 2
        img = img.crop((left, 0, left + target_w, h))
    else:
        # Taller/narrower than 4:3 — crop height down, keep full width.
        target_h = round(w / TARGET_RATIO)
        top = (h - target_h) // 2
        img = img.crop((0, top, w, top + target_h))

    if img.size[0] > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
