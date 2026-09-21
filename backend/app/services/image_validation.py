"""Is this really a photo we can safely handle? Pure standard library: no image library, no decoding.

A file is accepted only if its own bytes say it is a JPEG, PNG or WebP (never because of its name or the
type the browser claimed), it is not empty or larger than the limit, its header parses to real dimensions
inside the limits, and nothing about it looks like an executable, script or archive dressed up as an image.
The bytes are never run, decoded or rendered here; only the header is read. Anything else is refused with a
plain message and a code the screens use (`image_too_large`, `image_unsupported_type`, `image_corrupt`,
`image_dimensions`, `image_empty`).
"""

import base64
import binascii
import hashlib
import struct
from dataclasses import dataclass

from app.services.errors import InvalidInputError

ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MIN_SIDE = 32
MAX_PIXELS = 60_000_000
# What a file that is NOT an image starts with: executables, scripts, archives, documents.
_DANGEROUS_STARTS = (
    b"MZ",
    b"\x7fELF",
    b"#!",
    b"PK\x03\x04",
    b"%PDF",
    b"<?",
    b"<!",
    b"<s",
    b"<h",
    b"\xca\xfe\xba\xbe",
)


@dataclass(frozen=True)
class ImageInfo:
    content_type: str
    extension: str
    width: int
    height: int
    size_bytes: int
    sha256: str


def _bad(message: str, code: str) -> InvalidInputError:
    return InvalidInputError(message, field="image", code=code)


def decode_base64(text: str, *, max_bytes: int) -> bytes:
    """Decode a photo sent as base64 (or a data: URL). Refuses an over-large one before decoding."""
    payload = text.split(",", 1)[1] if text.startswith("data:") and "," in text else text
    payload = "".join(payload.split())
    if len(payload) > (max_bytes * 4) // 3 + 8:
        raise _bad(f"The image is too large (at most {max_bytes // 1_000_000} MB).", "image_too_large")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise _bad("The image could not be read.", "image_corrupt") from None


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("no IHDR")
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise ValueError("no frame header")


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    raise ValueError("unknown WebP")


_READERS = {"image/png": _png_size, "image/jpeg": _jpeg_size, "image/webp": _webp_size}


def inspect_image(data: bytes, *, declared_type: str | None, max_bytes: int, max_side: int) -> ImageInfo:
    """Check a photo's bytes and say what it is. Raises `InvalidInputError` (with a code) if not safe."""
    if not data:
        raise _bad("The image is empty.", "image_empty")
    if len(data) > max_bytes:
        raise _bad(f"The image is too large (at most {max_bytes // 1_000_000} MB).", "image_too_large")
    if data.startswith(_DANGEROUS_STARTS):
        raise _bad("This file is not a photo. Choose a JPEG, PNG or WebP image.", "image_unsupported_type")
    kind = _sniff(data)
    if kind is None:
        raise _bad(
            "This file is not a supported image. Choose a JPEG, PNG or WebP photo.", "image_unsupported_type"
        )
    if declared_type and declared_type.split(";")[0].strip().lower() not in (
        kind,
        "application/octet-stream",
        "",
    ):
        raise _bad("The file does not match the type it was sent as.", "image_unsupported_type")
    try:
        width, height = _READERS[kind](data)
    except (ValueError, struct.error, IndexError):
        raise _bad("The image is damaged and could not be read.", "image_corrupt") from None
    if width < MIN_SIDE or height < MIN_SIDE:
        raise _bad(f"The image is too small (at least {MIN_SIDE} pixels on each side).", "image_dimensions")
    if max(width, height) > max_side or width * height > MAX_PIXELS:
        raise _bad(
            f"The image is too large in pixels (at most {max_side} on the longest side).", "image_dimensions"
        )
    return ImageInfo(kind, ALLOWED[kind], width, height, len(data), hashlib.sha256(data).hexdigest())
