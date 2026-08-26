"""Lightroom Classic catalog (.lrcat) introspection.

The catalog is a SQLite database. Adobe does not document the schema and
changes it between versions, so this module never assumes a table exists — it
introspects what is present and extracts only what it finds. That way the tool
degrades to "here is what your catalog version contains" instead of crashing.

Two things live in the catalog that the sidecars cannot give you:

* **Pick / reject flags.** These are catalog state. If your cull used flags
  rather than stars, the sidecars will look empty and the catalog is the only
  place the decision survives.
* **Develop history.** Lightroom keeps per-image edit steps with timestamps,
  which is the closest thing to a retroactive record of "what changed after the
  automated pass ran".

Develop settings themselves are stored as a serialized Lua table, not XML, so
this module reports their presence and size rather than pretending to decode
them — XMP sidecars are the practical route to slider values.

The catalog is always copied before opening, and opened read-only. Lightroom
must be closed, or SQLite will refuse on the lock.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

# Columns worth pulling if the installed schema happens to have them.
INTERESTING = {
    "Adobe_images": [
        "id_local", "rating", "colorLabels", "pick", "captureTime",
        "fileFormat", "touchTime", "rootFile", "masterImage",
    ],
    "AgLibraryFile": ["id_local", "baseName", "extension", "folder", "importHash"],
    "AgLibraryFolder": ["id_local", "pathFromRoot", "rootFolder"],
    "AgLibraryRootFolder": ["id_local", "absolutePath", "name"],
    "Adobe_imageDevelopSettings": ["id_local", "image", "digest", "hasDevelopAdjustments"],
    "Adobe_libraryImageDevelopHistoryStep": [
        "id_local", "image", "dateCreated", "name", "relValueString",
    ],
}


def _tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = [r[0] for r in cur.fetchall()]
    schema: dict[str, list[str]] = {}
    for name in names:
        try:
            cur = conn.execute(f'PRAGMA table_info("{name}")')
            schema[name] = [r[1] for r in cur.fetchall()]
        except sqlite3.Error:
            schema[name] = []
    return schema


def inspect_catalog(lrcat: Path, sample_rows: int = 0) -> dict:
    """Report what a catalog contains without modifying it."""
    result: dict = {"catalog": str(lrcat), "readable": False, "tables": {}, "findings": []}

    if not lrcat.exists():
        result["error"] = "catalog file not found"
        return result

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / lrcat.name
        try:
            shutil.copy2(lrcat, work)
        except OSError as exc:
            result["error"] = f"could not copy catalog: {exc}"
            return result

        try:
            conn = sqlite3.connect(f"file:{work}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            result["error"] = f"could not open catalog: {exc}"
            return result

        try:
            schema = _tables(conn)
            result["readable"] = True
            result["table_count"] = len(schema)

            for table, wanted in INTERESTING.items():
                if table not in schema:
                    result["tables"][table] = {"present": False}
                    continue
                cols = schema[table]
                available = [c for c in wanted if c in cols]
                entry: dict = {"present": True, "columns_found": available}
                try:
                    entry["rows"] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                except sqlite3.Error:
                    entry["rows"] = None
                result["tables"][table] = entry

            images = result["tables"].get("Adobe_images", {})
            if images.get("present"):
                cols = images["columns_found"]
                if "pick" in cols:
                    try:
                        rows = conn.execute(
                            "SELECT pick, COUNT(*) FROM Adobe_images GROUP BY pick"
                        ).fetchall()
                        dist = {str(r[0]): r[1] for r in rows}
                        result["pick_distribution"] = dist
                        flagged = sum(v for k, v in dist.items() if k not in ("0", "0.0", "None"))
                        if flagged:
                            result["findings"].append(
                                f"{flagged:,} images carry a pick/reject flag. Flags are "
                                "catalog-only state — they are NOT in the XMP sidecars, so if "
                                "your cull used flags, this catalog is the only place that "
                                "decision survives."
                            )
                        else:
                            result["findings"].append(
                                "No pick/reject flags set — your cull decisions live in "
                                "ratings/labels or in the delivered set, not in flags."
                            )
                    except sqlite3.Error:
                        pass

                if "rating" in cols:
                    try:
                        rows = conn.execute(
                            "SELECT rating, COUNT(*) FROM Adobe_images GROUP BY rating"
                        ).fetchall()
                        result["rating_distribution"] = {str(r[0]): r[1] for r in rows}
                    except sqlite3.Error:
                        pass

                if "colorLabels" in cols:
                    try:
                        rows = conn.execute(
                            "SELECT colorLabels, COUNT(*) FROM Adobe_images "
                            "GROUP BY colorLabels ORDER BY COUNT(*) DESC LIMIT 12"
                        ).fetchall()
                        result["color_label_distribution"] = {str(r[0]): r[1] for r in rows}
                    except sqlite3.Error:
                        pass

            hist = result["tables"].get("Adobe_libraryImageDevelopHistoryStep", {})
            if hist.get("present") and hist.get("rows"):
                result["findings"].append(
                    f"{hist['rows']:,} develop-history steps are stored. Each carries a "
                    "timestamp, so the order in which edits were applied is partially "
                    "reconstructable — the closest thing in the archive to a retroactive "
                    "record of what you changed after an automated pass."
                )

            dev = result["tables"].get("Adobe_imageDevelopSettings", {})
            if dev.get("present"):
                result["findings"].append(
                    "Develop settings are present but Lightroom serializes them as a Lua "
                    "table, not XML. Extract slider values from XMP sidecars instead — "
                    "select all in Lightroom and press Ctrl/Cmd+S to write them out."
                )
        finally:
            conn.close()

    return result
