"""Sidecar snapshots — capturing disagreement data going forward.

The single most valuable training signal McKinley described is *where he
overrode the tool*: "Aftershoot said 2 stars, I said 5." That signal is not
recoverable from a finished archive, because an XMP sidecar holds one state —
the last one written. The AI's original suggestion is overwritten by the
correction that follows it.

It is, however, trivial to capture prospectively. Snapshot the sidecars the
moment the automated cull finishes and before any manual correction, then
snapshot again after. The diff of those two states is a labelled disagreement
set: every frame where the machine and the photographer parted ways, which is
exactly the set worth the most per example.

Cost: two commands per wedding. Value: the dataset that makes the model
personal rather than generic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .scan import find_files
from .xmp import guess_writer, parse_xmp

SNAPSHOT_EXTS = {".xmp"}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def take_snapshot(
    roots: list[Path],
    out_dir: Path,
    tag: str,
    copy_files: bool = True,
) -> dict:
    """Record the current rating/label/develop state of every sidecar found.

    ``tag`` names the stage, e.g. ``post-ai`` or ``post-review``.
    """
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    snap_dir = out_dir / f"{stamp}_{tag}"
    snap_dir.mkdir(parents=True, exist_ok=True)

    entries: dict[str, dict] = {}
    sidecars = find_files(roots, SNAPSHOT_EXTS)

    for path in sidecars:
        doc = parse_xmp(path)
        key = path.stem.lower()
        entries[key] = {
            "path": str(path),
            "sha256_16": _digest(path),
            "mtime": path.stat().st_mtime if path.exists() else None,
            "rating": doc.rating,
            "label": doc.label,
            "writer": guess_writer(doc),
            "has_nontrivial_develop": doc.has_nontrivial_develop,
            "develop": doc.develop_values(),
            "crop": doc.crop_values(),
            "mask_count": doc.mask_count,
            "grayscale": doc.converted_to_grayscale,
        }
        if copy_files:
            try:
                shutil.copy2(path, snap_dir / path.name)
            except OSError:
                pass

    manifest = {
        "tag": tag,
        "taken": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(r) for r in roots],
        "sidecar_count": len(entries),
        "entries": entries,
    }
    (snap_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    return {"dir": str(snap_dir), "count": len(entries), "manifest": manifest}


def _load_manifest(path: Path) -> dict:
    if path.is_dir():
        path = path / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def diff_snapshots(before: Path, after: Path, out_path: Path | None = None) -> dict:
    """Compare two snapshots and emit the disagreement set.

    Produces one record per frame whose rating, colour label, or develop state
    changed between the two captures — the supervised signal for "what the
    photographer changed about the machine's proposal".
    """
    m_before = _load_manifest(before)
    m_after = _load_manifest(after)
    b_entries = m_before["entries"]
    a_entries = m_after["entries"]

    changes: list[dict] = []
    unchanged = 0

    for key, after_e in a_entries.items():
        before_e = b_entries.get(key)
        if before_e is None:
            changes.append({
                "stem": key,
                "change": "added",
                "rating_before": None,
                "rating_after": after_e["rating"],
                "label_before": None,
                "label_after": after_e["label"],
            })
            continue

        rating_changed = before_e["rating"] != after_e["rating"]
        label_changed = before_e["label"] != after_e["label"]
        develop_changed = before_e.get("develop") != after_e.get("develop")
        crop_changed = before_e.get("crop") != after_e.get("crop")

        if not (rating_changed or label_changed or develop_changed or crop_changed):
            unchanged += 1
            continue

        delta = None
        if rating_changed and before_e["rating"] is not None and after_e["rating"] is not None:
            delta = after_e["rating"] - before_e["rating"]

        changes.append({
            "stem": key,
            "path": after_e["path"],
            "change": "modified",
            "rating_before": before_e["rating"],
            "rating_after": after_e["rating"],
            "rating_delta": delta,
            "label_before": before_e["label"],
            "label_after": after_e["label"],
            "develop_changed": develop_changed,
            "crop_changed": crop_changed,
            "writer_before": before_e.get("writer"),
            "writer_after": after_e.get("writer"),
        })

    removed = [k for k in b_entries if k not in a_entries]

    promoted = sum(1 for c in changes if (c.get("rating_delta") or 0) > 0)
    demoted = sum(1 for c in changes if (c.get("rating_delta") or 0) < 0)

    result = {
        "before": {"tag": m_before["tag"], "taken": m_before["taken"], "count": len(b_entries)},
        "after": {"tag": m_after["tag"], "taken": m_after["taken"], "count": len(a_entries)},
        "unchanged": unchanged,
        "changed": len(changes),
        "removed": len(removed),
        "promoted": promoted,
        "demoted": demoted,
        "disagreement_rate": round(len(changes) / len(a_entries), 4) if a_entries else 0.0,
        "changes": changes,
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix == ".jsonl":
            with out_path.open("w", encoding="utf-8") as fh:
                for c in changes:
                    fh.write(json.dumps(c, default=str) + "\n")
        else:
            out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result
