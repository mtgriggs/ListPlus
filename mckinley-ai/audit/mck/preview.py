"""Extracting a viewable preview from a raw file, without decoding it.

Every camera raw carries one or more JPEG previews so the camera can show the
image on its back screen. Reading those is enormously cheaper than demosaicing
the raw, needs no third-party library, and produces something perfectly good
enough to judge an expression or a blink by.

Rather than implement CR2, CR3, NEF, ARW, DNG, RAF and ORF layouts separately,
this scans for embedded JPEG streams and takes the largest valid one. The
format-specific approach is more precise but breaks on every camera generation;
the scan works on anything that embeds a JPEG, which is everything.

The cost is that a malformed or truncated stream could be picked up, so each
candidate is validated by parsing its SOF marker for real dimensions before it
is accepted.
"""

from __future__ import annotations

import struct
from pathlib import Path

SOI = b"\xff\xd8\xff"
EOI = b"\xff\xd9"

# Previews sit near the start of every raw format in common use, so reading the
# head is almost always enough. The full file is only read if that finds nothing.
DEFAULT_HEAD_BYTES = 24 * 1024 * 1024

# Below this, a stream is a thumbnail rather than a preview and is not worth
# showing for a judgement about expression or focus.
MIN_PREVIEW_EDGE = 400


def _sof_dimensions(buf: bytes) -> tuple[int, int] | None:
    """Width and height from a JPEG's SOF marker, or None if it does not parse."""
    if not buf.startswith(b"\xff\xd8"):
        return None
    pos = 2
    end = len(buf)
    while pos + 4 <= end:
        if buf[pos] != 0xFF:
            pos += 1
            continue
        marker = buf[pos + 1]
        while marker == 0xFF and pos + 2 < end:
            pos += 1
            marker = buf[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9:
            return None
        if pos + 4 > end:
            return None
        (length,) = struct.unpack_from(">H", buf, pos + 2)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > end:
                return None
            height, width = struct.unpack_from(">HH", buf, pos + 5)
            return width, height
        if length < 2:
            return None
        pos += 2 + length
    return None


def find_jpeg_streams(buf: bytes, limit: int = 12) -> list[tuple[int, int, int, int]]:
    """Locate embedded JPEG streams.

    Returns (start, end, width, height) for each stream that parses, largest
    first. Streams that do not yield an SOF are discarded rather than trusted.
    """
    found: list[tuple[int, int, int, int]] = []
    pos = 0
    while len(found) < limit:
        start = buf.find(SOI, pos)
        if start < 0:
            break
        end = buf.find(EOI, start + 2)
        if end < 0:
            break
        end += 2
        candidate = buf[start:end]
        dims = _sof_dimensions(candidate)
        if dims and min(dims) >= MIN_PREVIEW_EDGE:
            found.append((start, end, dims[0], dims[1]))
        # Continue past this stream's header rather than its end: some formats
        # nest a thumbnail inside the preview's APP segments, and skipping to
        # `end` would step over the larger stream that encloses it.
        pos = start + 2
    found.sort(key=lambda t: t[2] * t[3], reverse=True)
    return found


def extract_preview(
    path: Path,
    head_bytes: int = DEFAULT_HEAD_BYTES,
    max_edge: int | None = None,
) -> tuple[bytes, int, int] | None:
    """Largest embedded JPEG preview from a raw or JPEG file.

    Returns (jpeg_bytes, width, height), or None when nothing usable is found.
    ``max_edge`` is advisory: this does not re-encode, so it only influences
    which of several embedded sizes is preferred.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            buf = fh.read(head_bytes)
            streams = find_jpeg_streams(buf)
            if not streams and size > head_bytes:
                fh.seek(0)
                buf = fh.read()
                streams = find_jpeg_streams(buf)
    except OSError:
        return None

    if not streams:
        return None

    chosen = streams[0]
    if max_edge:
        # Prefer the smallest stream that still clears max_edge, so a 6000px
        # preview is not shipped to a browser that will draw it at 600.
        big_enough = [s for s in streams if max(s[2], s[3]) >= max_edge]
        if big_enough:
            chosen = min(big_enough, key=lambda s: s[2] * s[3])

    start, end, width, height = chosen
    return buf[start:end], width, height


def preview_available(path: Path) -> bool:
    return extract_preview(path, head_bytes=8 * 1024 * 1024) is not None
