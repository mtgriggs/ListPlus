"""Writing ratings and labels back into XMP sidecars — carefully.

Everything else in this toolkit is read-only. This module is the exception, and
it is the one place that can damage an archive, so it is deliberately
conservative:

* **Nothing is written without an explicit opt-in.** The callers default to
  producing a proposal file instead.
* **Existing sidecars are copied to a backup directory before any change.**
* **Edits are textual, not a re-serialization.** Rewriting the XML through a
  parser would reorder attributes, drop comments, and risk losing vendor
  namespaces the parser did not model. Substituting one attribute value in
  place leaves every other byte untouched.

That last point matters more than it sounds. A sidecar can carry develop
settings, masks, and history that took real work to produce; a "helpful"
rewrite that loses them is unrecoverable.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MINIMAL_SIDECAR = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="McKinley AI">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmp:CreatorTool="McKinley AI"
    xmp:Rating="{rating}"{label}>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""

VALID_LABELS = {"Red", "Yellow", "Green", "Blue", "Purple"}


@dataclass
class WriteResult:
    path: Path
    action: str              # created | updated | unchanged | skipped | error
    detail: str = ""


def _sub_attribute(text: str, name: str, value: str) -> tuple[str, bool]:
    """Replace an attribute value on rdf:Description, or report it absent."""
    pattern = re.compile(rf'(\b{re.escape(name)}=")([^"]*)(")')
    if pattern.search(text):
        return pattern.sub(rf'\g<1>{value}\g<3>', text, count=1), True
    return text, False


def _sub_element(text: str, name: str, value: str) -> tuple[str, bool]:
    """Replace an element's text content, or report it absent."""
    pattern = re.compile(rf'(<{re.escape(name)}>)(.*?)(</{re.escape(name)}>)', re.S)
    if pattern.search(text):
        return pattern.sub(rf'\g<1>{value}\g<3>', text, count=1), True
    return text, False


def _insert_attribute(text: str, name: str, value: str) -> tuple[str, bool]:
    """Add an attribute to the first rdf:Description open tag."""
    match = re.search(r'<rdf:Description\b', text)
    if not match:
        return text, False
    insert_at = match.end()
    return text[:insert_at] + f' {name}="{value}"' + text[insert_at:], True


def _set_field(text: str, name: str, value: str | None) -> str:
    """Set ``name`` to ``value`` in whichever form the file already uses."""
    if value is None:
        return text
    text, done = _sub_attribute(text, name, value)
    if done:
        return text
    text, done = _sub_element(text, name, value)
    if done:
        return text
    text, _ = _insert_attribute(text, name, value)
    return text


def write_rating(
    raw_path: Path,
    rating: int | None = None,
    label: str | None = None,
    backup_dir: Path | None = None,
    dry_run: bool = True,
) -> WriteResult:
    """Set the rating and/or colour label on a frame's sidecar.

    ``dry_run`` defaults to True: the caller must deliberately turn it off.
    When an existing sidecar is modified, it is copied to ``backup_dir`` first;
    refusing to proceed without one is intentional.
    """
    if rating is not None and not 0 <= rating <= 5:
        return WriteResult(raw_path, "error", f"rating {rating} out of range 0-5")
    if label is not None and label not in VALID_LABELS:
        return WriteResult(raw_path, "error", f"unknown label {label!r}")

    sidecar = raw_path.with_suffix(".xmp")
    if not sidecar.exists():
        alt = raw_path.with_suffix(".XMP")
        if alt.exists():
            sidecar = alt

    if sidecar.exists():
        try:
            original = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return WriteResult(sidecar, "error", f"read failed: {exc}")

        updated = original
        if rating is not None:
            updated = _set_field(updated, "xmp:Rating", str(rating))
        if label is not None:
            updated = _set_field(updated, "xmp:Label", label)

        if updated == original:
            return WriteResult(sidecar, "unchanged")
        if dry_run:
            return WriteResult(sidecar, "updated", "dry run — not written")

        if backup_dir is None:
            return WriteResult(sidecar, "skipped", "refusing to modify without a backup dir")
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(sidecar, backup_dir / sidecar.name)
            sidecar.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return WriteResult(sidecar, "error", f"write failed: {exc}")
        return WriteResult(sidecar, "updated")

    # No sidecar yet — create a minimal one. Nothing can be lost here.
    if dry_run:
        return WriteResult(sidecar, "created", "dry run — not written")
    label_attr = f'\n    xmp:Label="{label}"' if label else ""
    try:
        sidecar.write_text(
            MINIMAL_SIDECAR.format(rating=rating if rating is not None else 0,
                                   label=label_attr),
            encoding="utf-8",
        )
    except OSError as exc:
        return WriteResult(sidecar, "error", f"create failed: {exc}")
    return WriteResult(sidecar, "created")


def write_many(
    decisions: list[tuple[Path, int | None, str | None]],
    backup_root: Path,
    dry_run: bool = True,
) -> dict:
    """Apply many rating decisions, into one timestamped backup directory."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = backup_root / f"xmp-backup-{stamp}"

    results = [
        write_rating(path, rating, label, backup_dir=backup_dir, dry_run=dry_run)
        for path, rating, label in decisions
    ]

    counts: dict[str, int] = {}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1

    return {
        "dry_run": dry_run,
        "backup_dir": str(backup_dir) if not dry_run else None,
        "counts": counts,
        "errors": [{"path": str(r.path), "detail": r.detail}
                   for r in results if r.action == "error"],
    }
