"""Capture-time and camera metadata extraction.

Capture time is the highest-value field in the whole audit after the keep/drop
label itself. It is what makes burst detection possible, and burst structure is
where the personal signal lives: within a run of eight near-identical frames,
which one got delivered is a decision no generic aesthetic model can make.

Two backends:

* ``exiftool`` when it is on PATH — complete, handles every raw format, and is
  run in batch mode so thousands of files cost one process.
* A stdlib fallback that reads TIFF-structured raws (CR2/NEF/ARW/DNG/ORF/PEF),
  Canon CR3 (ISO-BMFF), and JPEG. Narrower coverage, zero install.

The audit reports which backend produced each row and what fraction of files
yielded a timestamp, so thin coverage is visible rather than silent.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# --- EXIF tag ids we care about ------------------------------------------

TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_SOFTWARE = 0x0131
TAG_MODIFY_DATE = 0x0132
TAG_EXIF_IFD = 0x8769
TAG_EXPOSURE_TIME = 0x829A
TAG_FNUMBER = 0x829D
TAG_ISO = 0x8827
TAG_ISO_SENSITIVITY = 0x8833
TAG_DATETIME_ORIGINAL = 0x9003
TAG_CREATE_DATE = 0x9004
TAG_SHUTTER_SPEED_VALUE = 0x9201
TAG_FLASH = 0x9209
TAG_FOCAL_LENGTH = 0x920A
TAG_SUBSEC_ORIGINAL = 0x9291
TAG_OFFSET_TIME_ORIGINAL = 0x9011
TAG_BODY_SERIAL = 0xA431
TAG_LENS_MODEL = 0xA434
TAG_LENS_SERIAL = 0xA435

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

RAW_EXTS = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".pef", ".raf", ".rw2", ".raw", ".3fr", ".iiq",
}
JPEG_EXTS = {".jpg", ".jpeg", ".jpe"}


@dataclass
class ExifRecord:
    """Normalized camera metadata for one file."""

    source: str = "none"          # exiftool | builtin | none
    capture_time: str | None = None   # ISO-8601, no timezone applied
    capture_epoch: float | None = None
    subsec: str | None = None
    tz_offset: str | None = None
    make: str | None = None
    model: str | None = None
    body_serial: str | None = None
    lens: str | None = None
    iso: int | None = None
    aperture: float | None = None
    shutter: float | None = None      # seconds
    focal_length: float | None = None
    flash_fired: bool | None = None
    software: str | None = None
    width: int | None = None
    height: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def body_key(self) -> str:
        """Identity of the physical camera body, for per-body burst grouping.

        Two shooters firing simultaneously interleave in filename order; without
        this, their frames would be merged into nonsense bursts.
        """
        return self.body_serial or f"{self.make or '?'}|{self.model or '?'}"


# --- exiftool backend -----------------------------------------------------

EXIFTOOL_FIELDS = [
    "-DateTimeOriginal", "-CreateDate", "-SubSecTimeOriginal", "-OffsetTimeOriginal",
    "-Make", "-Model", "-SerialNumber", "-InternalSerialNumber",
    "-LensModel", "-Lens", "-LensID",
    "-ISO", "-FNumber", "-ExposureTime", "-FocalLength", "-Flash",
    "-Software", "-ImageWidth", "-ImageHeight",
]


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def _parse_dt(value: str | None) -> tuple[str | None, float | None]:
    """EXIF ``YYYY:MM:DD HH:MM:SS`` -> (ISO string, epoch seconds)."""
    if not value:
        return None, None
    value = value.strip()
    if value.startswith("0000"):
        return None, None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(value[:19], fmt[:19] if "%z" not in fmt else fmt)
            return dt.isoformat(), dt.timestamp()
        except ValueError:
            continue
    return None, None


def _as_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if "/" in s:
        try:
            num, den = s.split("/", 1)
            den = float(den)
            return float(num) / den if den else None
        except ValueError:
            return None
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return None


def read_exif_batch_exiftool(paths: list[Path]) -> dict[Path, ExifRecord]:
    """One exiftool invocation for many files."""
    out: dict[Path, ExifRecord] = {}
    if not paths:
        return out
    cmd = ["exiftool", "-j", "-n", "-q", "-fast2", *EXIFTOOL_FIELDS, *[str(p) for p in paths]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        data = json.loads(proc.stdout) if proc.stdout.strip() else []
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return out

    by_name = {p.name: p for p in paths}
    for item in data:
        src = item.get("SourceFile", "")
        path = Path(src)
        if path not in by_name.values():
            path = by_name.get(Path(src).name, path)

        iso_str, epoch = _parse_dt(item.get("DateTimeOriginal") or item.get("CreateDate"))
        flash_raw = item.get("Flash")
        flash = None
        if flash_raw is not None:
            try:
                flash = bool(int(flash_raw) & 1)
            except (TypeError, ValueError):
                flash = "did not fire" not in str(flash_raw).lower()

        out[path] = ExifRecord(
            source="exiftool",
            capture_time=iso_str,
            capture_epoch=epoch,
            subsec=str(item["SubSecTimeOriginal"]) if item.get("SubSecTimeOriginal") is not None else None,
            tz_offset=item.get("OffsetTimeOriginal"),
            make=item.get("Make"),
            model=item.get("Model"),
            body_serial=str(item.get("SerialNumber") or item.get("InternalSerialNumber") or "") or None,
            lens=item.get("LensModel") or item.get("Lens") or item.get("LensID"),
            iso=int(_as_float(item.get("ISO"))) if _as_float(item.get("ISO")) else None,
            aperture=_as_float(item.get("FNumber")),
            shutter=_as_float(item.get("ExposureTime")),
            focal_length=_as_float(item.get("FocalLength")),
            flash_fired=flash,
            software=item.get("Software"),
            width=int(_as_float(item.get("ImageWidth")) or 0) or None,
            height=int(_as_float(item.get("ImageHeight")) or 0) or None,
        )
    return out


# --- stdlib fallback backend ---------------------------------------------


def _read_ifd(buf: bytes, offset: int, endian: str, tags: dict, depth: int = 0) -> None:
    """Read one TIFF IFD into ``tags``; follows the Exif sub-IFD pointer."""
    if depth > 3 or offset <= 0 or offset + 2 > len(buf):
        return
    try:
        (count,) = struct.unpack_from(endian + "H", buf, offset)
    except struct.error:
        return
    pos = offset + 2
    for _ in range(count):
        if pos + 12 > len(buf):
            return
        tag, typ, n = struct.unpack_from(endian + "HHI", buf, pos)
        value_off = pos + 8
        size = TYPE_SIZES.get(typ, 0) * n
        if size == 0:
            pos += 12
            continue
        if size > 4:
            try:
                (ptr,) = struct.unpack_from(endian + "I", buf, value_off)
            except struct.error:
                pos += 12
                continue
            value_off = ptr
        if value_off + size > len(buf) or value_off < 0:
            pos += 12
            continue

        raw = buf[value_off:value_off + size]
        try:
            if typ == 2:
                val = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
            elif typ in (3, 8):
                val = struct.unpack_from(endian + ("H" if typ == 3 else "h"), raw, 0)[0]
            elif typ in (4, 9):
                val = struct.unpack_from(endian + ("I" if typ == 4 else "i"), raw, 0)[0]
            elif typ in (5, 10):
                num, den = struct.unpack_from(endian + ("II" if typ == 5 else "ii"), raw, 0)
                val = (num / den) if den else None
            elif typ == 11:
                val = struct.unpack_from(endian + "f", raw, 0)[0]
            elif typ == 12:
                val = struct.unpack_from(endian + "d", raw, 0)[0]
            else:
                val = raw
        except struct.error:
            pos += 12
            continue

        tags.setdefault(tag, val)
        if tag == TAG_EXIF_IFD and isinstance(val, int):
            _read_ifd(buf, val, endian, tags, depth + 1)
        pos += 12


def _parse_tiff(buf: bytes) -> dict:
    """Parse a TIFF header + IFD0 (and Exif IFD) from the start of ``buf``."""
    tags: dict = {}
    if len(buf) < 8:
        return tags
    if buf[:2] == b"II":
        endian = "<"
    elif buf[:2] == b"MM":
        endian = ">"
    else:
        return tags
    try:
        (ifd0,) = struct.unpack_from(endian + "I", buf, 4)
    except struct.error:
        return tags
    _read_ifd(buf, ifd0, endian, tags)
    return tags


def _parse_jpeg(buf: bytes) -> dict:
    """Find the APP1/Exif segment in a JPEG and parse its TIFF payload."""
    if buf[:2] != b"\xff\xd8":
        return {}
    pos = 2
    while pos + 4 <= len(buf):
        if buf[pos] != 0xFF:
            pos += 1
            continue
        marker = buf[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:  # start of scan — no metadata past here
            break
        if pos + 4 > len(buf):
            break
        (seg_len,) = struct.unpack_from(">H", buf, pos + 2)
        seg = buf[pos + 4: pos + 2 + seg_len]
        if marker == 0xE1 and seg[:6] == b"Exif\x00\x00":
            return _parse_tiff(seg[6:])
        pos += 2 + seg_len
    return {}


# Canon CR3 stores its EXIF IFDs inside a uuid box in `moov`.
CR3_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")


def _parse_cr3(buf: bytes) -> dict:
    """Walk ISO-BMFF boxes to locate CMT1 (IFD0) and CMT2 (Exif IFD)."""
    tags: dict = {}

    def walk(start: int, end: int, depth: int = 0) -> None:
        if depth > 6:
            return
        pos = start
        while pos + 8 <= end:
            try:
                size, kind = struct.unpack_from(">I4s", buf, pos)
            except struct.error:
                return
            header = 8
            if size == 1:
                if pos + 16 > end:
                    return
                (size,) = struct.unpack_from(">Q", buf, pos + 8)
                header = 16
            elif size == 0:
                size = end - pos
            if size < header or pos + size > end:
                return

            body_start, body_end = pos + header, pos + size

            if kind in (b"CMT1", b"CMT2", b"CMT3", b"CMT4"):
                tags.update({k: v for k, v in _parse_tiff(buf[body_start:body_end]).items()
                             if k not in tags})
            elif kind == b"uuid":
                if buf[body_start:body_start + 16] == CR3_UUID:
                    walk(body_start + 16, body_end, depth + 1)
            elif kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta"):
                walk(body_start, body_end, depth + 1)

            pos += size

    walk(0, len(buf))
    return tags


def read_exif_builtin(path: Path, head_bytes: int = 1024 * 512) -> ExifRecord:
    """Parse metadata from the head of a file without external tools."""
    rec = ExifRecord(source="builtin")
    ext = path.suffix.lower()
    try:
        with path.open("rb") as fh:
            buf = fh.read(head_bytes)
    except OSError:
        rec.source = "none"
        return rec

    if ext == ".cr3":
        tags = _parse_cr3(buf)
    elif ext in JPEG_EXTS:
        tags = _parse_jpeg(buf)
    elif ext in RAW_EXTS or buf[:2] in (b"II", b"MM"):
        tags = _parse_tiff(buf)
    else:
        tags = {}

    if not tags:
        rec.source = "none"
        return rec

    dt_raw = tags.get(TAG_DATETIME_ORIGINAL) or tags.get(TAG_CREATE_DATE) or tags.get(TAG_MODIFY_DATE)
    iso_str, epoch = _parse_dt(dt_raw if isinstance(dt_raw, str) else None)

    flash_raw = tags.get(TAG_FLASH)
    flash = bool(int(flash_raw) & 1) if isinstance(flash_raw, int) else None

    iso_val = tags.get(TAG_ISO) or tags.get(TAG_ISO_SENSITIVITY)

    rec.capture_time = iso_str
    rec.capture_epoch = epoch
    rec.subsec = str(tags[TAG_SUBSEC_ORIGINAL]) if tags.get(TAG_SUBSEC_ORIGINAL) is not None else None
    rec.tz_offset = tags.get(TAG_OFFSET_TIME_ORIGINAL) if isinstance(tags.get(TAG_OFFSET_TIME_ORIGINAL), str) else None
    rec.make = tags.get(TAG_MAKE) if isinstance(tags.get(TAG_MAKE), str) else None
    rec.model = tags.get(TAG_MODEL) if isinstance(tags.get(TAG_MODEL), str) else None
    rec.body_serial = str(tags[TAG_BODY_SERIAL]) if tags.get(TAG_BODY_SERIAL) else None
    rec.lens = tags.get(TAG_LENS_MODEL) if isinstance(tags.get(TAG_LENS_MODEL), str) else None
    rec.iso = int(iso_val) if isinstance(iso_val, (int, float)) else None
    rec.aperture = _as_float(tags.get(TAG_FNUMBER))
    rec.shutter = _as_float(tags.get(TAG_EXPOSURE_TIME))
    rec.focal_length = _as_float(tags.get(TAG_FOCAL_LENGTH))
    rec.flash_fired = flash
    rec.software = tags.get(TAG_SOFTWARE) if isinstance(tags.get(TAG_SOFTWARE), str) else None
    return rec


def read_exif_many(paths: list[Path], prefer_exiftool: bool = True) -> dict[Path, ExifRecord]:
    """Read metadata for many files, using the best available backend."""
    if prefer_exiftool and exiftool_available():
        result = read_exif_batch_exiftool(paths)
        # Fill any gaps exiftool did not return with the builtin reader.
        for p in paths:
            if p not in result or result[p].capture_epoch is None:
                fallback = read_exif_builtin(p)
                if fallback.capture_epoch is not None or p not in result:
                    result[p] = fallback if p not in result else result[p]
        return result
    return {p: read_exif_builtin(p) for p in paths}
