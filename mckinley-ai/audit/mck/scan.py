"""Wedding folder scanning and the source -> delivered join.

The join is the whole ballgame. The ground-truth label for culling is not a star
rating and not a colour label — it is *did this frame end up in the gallery the
client received*. That label already exists for every wedding ever delivered,
needs no extra work, and cannot be contaminated by a tool's suggestion.

Matching delivered exports back to source frames has two strategies, applied in
order:

1. **Filename stem** — works when exports keep the camera's filename.
2. **Capture timestamp** — works when exports were renamed (``Smith-0123.jpg``),
   which is the common case for a delivered gallery. Matching on
   DateTimeOriginal (plus sub-second and body serial when present) survives any
   renaming scheme.

Both match rates are reported, because a low rate is itself a finding: it means
the archive's delivered sets cannot be joined and the label has to come from
somewhere else.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .bursts import DEFAULT_BURST_GAP, DEFAULT_SCENE_GAP, segment, summarize
from .exif import JPEG_EXTS, RAW_EXTS, ExifRecord, read_exif_many
from .xmp import XmpDoc, guess_writer, parse_xmp

# Suffixes Lightroom / Photoshop / editors append on export.
EXPORT_SUFFIX_RE = re.compile(
    r"(-edit|-edit-\d+|-enhanced(-nr)?|_edit|-copy|-\d{1,2}|_\d{1,2}|-bearbeitet)$",
    re.IGNORECASE,
)

# Timestamp tolerance when joining by capture time (seconds).
TIME_JOIN_TOLERANCE = 1.0


def normalize_stem(name: str) -> str:
    """Reduce an export filename to something comparable with a source stem."""
    stem = Path(name).stem
    prev = None
    while prev != stem:
        prev = stem
        stem = EXPORT_SUFFIX_RE.sub("", stem)
    return stem.lower()


@dataclass
class ImageRecord:
    """One source frame plus everything the audit knows about it."""

    path: Path
    stem: str
    ext: str
    size_bytes: int

    exif: ExifRecord = field(default_factory=ExifRecord)

    xmp_path: Path | None = None
    xmp: XmpDoc | None = None
    xmp_mtime: float | None = None

    delivered: bool = False
    delivered_path: Path | None = None
    match_method: str | None = None      # stem | time | none

    burst_id: int | None = None
    scene_id: int | None = None

    # -- convenience accessors used by the report --------------------------

    @property
    def rating(self) -> int | None:
        return self.xmp.rating if self.xmp else None

    @property
    def label(self) -> str | None:
        return self.xmp.label if self.xmp else None

    @property
    def writer(self) -> str:
        return guess_writer(self.xmp) if self.xmp else "no-sidecar"

    def to_row(self) -> dict:
        x = self.xmp
        row = {
            "stem": self.stem,
            "path": str(self.path),
            "ext": self.ext,
            "size_bytes": self.size_bytes,
            "capture_time": self.exif.capture_time,
            "capture_epoch": self.exif.capture_epoch,
            "camera_make": self.exif.make,
            "camera_model": self.exif.model,
            "body_serial": self.exif.body_serial,
            "lens": self.exif.lens,
            "iso": self.exif.iso,
            "aperture": self.exif.aperture,
            "shutter": self.exif.shutter,
            "focal_length": self.exif.focal_length,
            "flash_fired": self.exif.flash_fired,
            "exif_source": self.exif.source,
            "has_xmp": x is not None,
            "xmp_writer": self.writer,
            "xmp_mtime": self.xmp_mtime,
            "rating": self.rating,
            "label": self.label,
            "process_version": x.process_version if x else None,
            "has_develop": bool(x and x.has_develop),
            "has_nontrivial_develop": bool(x and x.has_nontrivial_develop),
            "already_applied": x.already_applied if x else None,
            "grayscale": x.converted_to_grayscale if x else None,
            "preset_name": x.preset_name if x else None,
            "mask_count": x.mask_count if x else None,
            "delivered": self.delivered,
            "delivered_path": str(self.delivered_path) if self.delivered_path else None,
            "match_method": self.match_method,
            "burst_id": self.burst_id,
            "scene_id": self.scene_id,
        }
        if x:
            for key, val in x.develop_values().items():
                row[key.replace(":", "_")] = val
            for key, val in x.crop_values().items():
                row[key.replace(":", "_")] = val
        return row


def find_files(roots: list[Path], exts: set[str], recursive: bool = True) -> list[Path]:
    """Collect files with the given extensions, skipping hidden directories."""
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in exts:
                found.append(root)
            continue
        walker = os.walk(root) if recursive else [(str(root), [], os.listdir(root))]
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() in exts:
                    found.append(Path(dirpath) / name)
    return sorted(set(found))


def locate_sidecar(raw_path: Path) -> Path | None:
    """Find the XMP sidecar for a raw file.

    Adobe writes ``IMG_1234.xmp`` (extension replaced). Some tools write
    ``IMG_1234.CR2.xmp`` (extension appended). Both are checked, case-insensitively.
    """
    candidates = [
        raw_path.with_suffix(".xmp"),
        raw_path.with_suffix(".XMP"),
        Path(str(raw_path) + ".xmp"),
        Path(str(raw_path) + ".XMP"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    # Case-insensitive sweep of the directory as a last resort.
    target = raw_path.stem.lower()
    try:
        for entry in raw_path.parent.iterdir():
            if entry.suffix.lower() == ".xmp" and entry.stem.lower() in (target, raw_path.name.lower()):
                return entry
    except OSError:
        pass
    return None


def join_by_stems(records: list[ImageRecord], stems: set[str]) -> dict:
    """Mark records whose filename stem appears in a catalog-derived label set.

    Used when the delivered gallery does not exist on disk. The catalog records
    decisions against the source frame, so the join is by stem and needs no
    exported JPEG to exist anywhere.
    """
    matched = 0
    for rec in records:
        if normalize_stem(rec.stem) in stems:
            rec.delivered = True
            rec.match_method = "catalog"
            matched += 1
    return {
        "delivered_files": len(stems),
        "matched_by_stem": 0,
        "matched_by_time": 0,
        "matched_by_catalog": matched,
        "unmatched": max(0, len(stems) - matched),
        "ambiguous_time": 0,
        "unmatched_examples": [],
    }


def join_delivered(
    records: list[ImageRecord],
    delivered_paths: list[Path],
    delivered_exif: dict[Path, ExifRecord],
) -> dict:
    """Mark records that appear in the delivered set. Returns join statistics."""
    by_stem: dict[str, list[ImageRecord]] = {}
    for rec in records:
        by_stem.setdefault(normalize_stem(rec.stem), []).append(rec)

    # Bucket source frames by whole-second capture time for the time-based pass.
    by_second: dict[int, list[ImageRecord]] = {}
    for rec in records:
        if rec.exif.capture_epoch is not None:
            by_second.setdefault(int(rec.exif.capture_epoch), []).append(rec)

    stats = {
        "delivered_files": len(delivered_paths),
        "matched_by_stem": 0,
        "matched_by_time": 0,
        "unmatched": 0,
        "ambiguous_time": 0,
        "unmatched_examples": [],
    }

    for dpath in delivered_paths:
        target = normalize_stem(dpath.name)
        bucket = by_stem.get(target)
        if bucket:
            for rec in bucket:
                if not rec.delivered:
                    rec.delivered = True
                    rec.delivered_path = dpath
                    rec.match_method = "stem"
                    break
            stats["matched_by_stem"] += 1
            continue

        drec = delivered_exif.get(dpath)
        epoch = drec.capture_epoch if drec else None
        if epoch is None:
            stats["unmatched"] += 1
            if len(stats["unmatched_examples"]) < 10:
                stats["unmatched_examples"].append(dpath.name)
            continue

        # Look in the surrounding second buckets to absorb rounding.
        base = int(epoch)
        candidates: list[ImageRecord] = []
        for offset in (-1, 0, 1):
            candidates.extend(by_second.get(base + offset, []))
        near = [
            c for c in candidates
            if c.exif.capture_epoch is not None
            and abs(c.exif.capture_epoch - epoch) <= TIME_JOIN_TOLERANCE
        ]
        if not near:
            stats["unmatched"] += 1
            if len(stats["unmatched_examples"]) < 10:
                stats["unmatched_examples"].append(dpath.name)
            continue

        if drec and drec.body_serial:
            same_body = [c for c in near if c.exif.body_serial == drec.body_serial]
            if same_body:
                near = same_body

        free = [c for c in near if not c.delivered]
        if len(near) > 1 and len(free) > 1:
            stats["ambiguous_time"] += 1

        chosen = free[0] if free else near[0]
        if not chosen.delivered:
            chosen.delivered = True
            chosen.delivered_path = dpath
            chosen.match_method = "time"
        stats["matched_by_time"] += 1

    return stats


def scan_wedding(
    raw_roots: list[Path],
    delivered_roots: list[Path],
    burst_gap: float = DEFAULT_BURST_GAP,
    scene_gap: float = DEFAULT_SCENE_GAP,
    prefer_exiftool: bool = True,
    limit: int | None = None,
    label_stems: set[str] | None = None,
) -> tuple[list[ImageRecord], dict]:
    """Build the full record set for one wedding, plus scan metadata."""
    raw_paths = find_files(raw_roots, RAW_EXTS | JPEG_EXTS)
    if limit:
        raw_paths = raw_paths[:limit]

    exif_map = read_exif_many(raw_paths, prefer_exiftool=prefer_exiftool)

    records: list[ImageRecord] = []
    for path in raw_paths:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rec = ImageRecord(
            path=path,
            stem=path.stem,
            ext=path.suffix.lower(),
            size_bytes=size,
            exif=exif_map.get(path, ExifRecord()),
        )
        sidecar = locate_sidecar(path)
        if sidecar:
            rec.xmp_path = sidecar
            rec.xmp = parse_xmp(sidecar)
            try:
                rec.xmp_mtime = sidecar.stat().st_mtime
            except OSError:
                rec.xmp_mtime = None
        records.append(rec)

    if label_stems is not None:
        # Catalog-derived label: no exported JPEGs need exist on disk.
        join_stats = join_by_stems(records, label_stems)
    else:
        delivered_paths = find_files(delivered_roots, JPEG_EXTS) if delivered_roots else []
        delivered_exif: dict[Path, ExifRecord] = {}
        if delivered_paths:
            # Only the ones that fail a stem match need EXIF, but reading all of
            # them in one batch is cheaper than deciding twice.
            delivered_exif = read_exif_many(delivered_paths, prefer_exiftool=prefer_exiftool)
        join_stats = join_delivered(records, delivered_paths, delivered_exif)

    epochs = [r.exif.capture_epoch for r in records]
    bodies = [r.exif.body_key for r in records]

    # Bursts are per-body (two shooters are never one burst); scenes are not
    # (a phase of the day is shared by everyone shooting it).
    bursts = segment(epochs, bodies, gap=burst_gap, kind="burst", per_body=True)
    scenes = segment(epochs, bodies, gap=scene_gap, kind="scene", per_body=False)
    for seg in bursts:
        for idx in seg.indices:
            records[idx].burst_id = seg.index
    for seg in scenes:
        for idx in seg.indices:
            records[idx].scene_id = seg.index

    kept = [r.delivered for r in records]
    meta = {
        "raw_roots": [str(p) for p in raw_roots],
        "delivered_roots": [str(p) for p in delivered_roots],
        "source_count": len(records),
        "join": join_stats,
        "bursts": summarize(bursts, kept),
        "scenes": summarize(scenes, kept),
        "burst_gap": burst_gap,
        "scene_gap": scene_gap,
    }
    return records, meta
