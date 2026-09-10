"""Lightroom Classic catalog (.lrcat) reading.

The catalog is a SQLite database. Adobe does not document the schema and
changes it between versions, so nothing here assumes a table or column exists.
Every query is guarded by introspection, and anything missing is reported as
missing rather than crashing the run.

**Why this module carries the project.** The original plan used the delivered
client gallery as the training label. When galleries live only in a hosting
service and never on disk, that label is unavailable locally and the catalog
becomes the only complete record of which frames were chosen. It is arguably a
better record anyway: it holds pick flags and collection membership, which a
folder of exported JPEGs cannot express, and it survives re-exports and
renames.

The catalog offers several candidate labels, and which one is right depends on
how the photographer actually worked. Rather than guess, ``discover_label_sources``
reports every candidate with its counts so the choice is made against evidence.

Develop settings are stored as a serialized Lua table rather than XML, so they
are reported as present but not decoded. XMP sidecars are the practical route
to slider values.

The catalog is copied before opening and opened read-only. Lightroom must be
closed or SQLite will refuse on the lock.
"""

from __future__ import annotations

import csv
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Columns worth pulling if the installed schema happens to have them.
INTERESTING = {
    "Adobe_images": [
        "id_local", "rating", "colorLabels", "pick", "captureTime",
        "fileFormat", "touchTime", "rootFile", "masterImage",
    ],
    "AgLibraryFile": ["id_local", "baseName", "extension", "folder", "originalFilename"],
    "AgLibraryFolder": ["id_local", "pathFromRoot", "rootFolder"],
    "AgLibraryRootFolder": ["id_local", "absolutePath", "name"],
    "AgLibraryCollection": ["id_local", "name", "parent", "systemOnly"],
    "AgLibraryCollectionImage": ["id_local", "collection", "image"],
    "AgLibraryPublishedCollection": ["id_local", "name", "remoteCollectionId"],
    "AgRemotePhoto": ["id_local", "collection", "photo", "mostRecentPublishTime", "remoteId"],
    "Adobe_imageDevelopSettings": ["id_local", "image", "digest", "hasDevelopAdjustments"],
    "Adobe_libraryImageDevelopHistoryStep": [
        "id_local", "image", "dateCreated", "name", "relValueString",
    ],
}

# Collection names that suggest "these are the frames the client received".
DELIVERY_NAME_HINTS = [
    "deliver", "final", "gallery", "export", "client", "pick", "select",
    "keeper", "edited", "sent", "proof", "pic-time", "pictime", "published",
]


@contextmanager
def open_catalog(lrcat: Path):
    """Copy the catalog to a temp dir and open it read-only."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / lrcat.name
        shutil.copy2(lrcat, work)
        conn = sqlite3.connect(f"file:{work}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def table_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    schema: dict[str, list[str]] = {}
    for (name,) in cur.fetchall():
        try:
            schema[name] = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
        except sqlite3.Error:
            schema[name] = []
    return schema


def _has(schema: dict[str, list[str]], table: str, *cols: str) -> bool:
    if table not in schema:
        return False
    return all(c in schema[table] for c in cols)


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


# --- label source discovery ----------------------------------------------


def discover_label_sources(conn: sqlite3.Connection) -> dict:
    """Enumerate every usable "which frames did I choose" signal in a catalog.

    Returns candidates ordered by how trustworthy they are as a delivered-set
    label. A published-collection record is the strongest: it is a literal log
    of what was uploaded. Ratings are the weakest, because a rating is an
    opinion recorded at cull time that may never have translated into delivery.
    """
    schema = table_columns(conn)
    total_images = _count(conn, "SELECT COUNT(*) FROM Adobe_images") if "Adobe_images" in schema else 0

    candidates: list[dict] = []

    # 1. Published photos: the strongest possible signal.
    if _has(schema, "AgRemotePhoto", "photo"):
        published = _count(conn, "SELECT COUNT(DISTINCT photo) FROM AgRemotePhoto")
        if published:
            services: list[dict] = []
            if _has(schema, "AgRemotePhoto", "collection") and \
               _has(schema, "AgLibraryPublishedCollection", "id_local", "name"):
                try:
                    rows = conn.execute(
                        "SELECT pc.name AS name, COUNT(DISTINCT rp.photo) AS n "
                        "FROM AgRemotePhoto rp "
                        "JOIN AgLibraryPublishedCollection pc ON pc.id_local = rp.collection "
                        "GROUP BY pc.name ORDER BY n DESC LIMIT 40"
                    ).fetchall()
                    services = [{"name": r["name"], "images": r["n"]} for r in rows]
                except sqlite3.Error:
                    pass
            candidates.append({
                "kind": "published",
                "key": "published",
                "label": "Published photos (publish service upload log)",
                "images": published,
                "share": round(published / total_images, 4) if total_images else 0.0,
                "strength": "strongest",
                "detail": ("A literal record of frames uploaded through a Lightroom publish "
                           "service. If galleries were delivered this way, this is the "
                           "delivered set, exactly."),
                "collections": services,
            })

    # 2. Collections whose names look like delivery sets.
    if _has(schema, "AgLibraryCollection", "id_local", "name") and \
       _has(schema, "AgLibraryCollectionImage", "collection", "image"):
        try:
            rows = conn.execute(
                "SELECT c.id_local AS id, c.name AS name, COUNT(ci.image) AS n "
                "FROM AgLibraryCollection c "
                "JOIN AgLibraryCollectionImage ci ON ci.collection = c.id_local "
                "GROUP BY c.id_local, c.name HAVING n > 0 ORDER BY n DESC LIMIT 200"
            ).fetchall()
        except sqlite3.Error:
            rows = []

        named = []
        for r in rows:
            name = (r["name"] or "").lower()
            if any(hint in name for hint in DELIVERY_NAME_HINTS):
                named.append({"id": r["id"], "name": r["name"], "images": r["n"]})
        if named:
            candidates.append({
                "kind": "collection",
                "key": "collection",
                "label": "Collections named like a delivery set",
                "images": sum(c["images"] for c in named),
                "share": 0.0,
                "strength": "strong",
                "detail": ("Collections whose names suggest they hold the chosen frames. "
                           "Confirm these are what they look like before trusting them."),
                "collections": named[:40],
            })
        candidates.append({
            "kind": "collection_all",
            "key": "collection_all",
            "label": "All collections",
            "images": sum(r["n"] for r in rows),
            "share": 0.0,
            "strength": "context",
            "detail": "Every non-empty collection, in case the naming convention is not obvious.",
            "collections": [{"id": r["id"], "name": r["name"], "images": r["n"]}
                            for r in rows[:60]],
        })

    # 3. Pick flags.
    if _has(schema, "Adobe_images", "pick"):
        picked = _count(conn, "SELECT COUNT(*) FROM Adobe_images WHERE pick > 0")
        rejected = _count(conn, "SELECT COUNT(*) FROM Adobe_images WHERE pick < 0")
        if picked or rejected:
            candidates.append({
                "kind": "pick",
                "key": "pick",
                "label": "Pick / reject flags",
                "images": picked,
                "share": round(picked / total_images, 4) if total_images else 0.0,
                "strength": "strong",
                "detail": (f"{picked:,} flagged picked, {rejected:,} flagged rejected. Flags are "
                           "catalog-only state and never appear in XMP sidecars, so if the cull "
                           "used flags this is the only place that decision survives."),
                "collections": [],
            })

    # 4. Colour labels.
    if _has(schema, "Adobe_images", "colorLabels"):
        try:
            rows = conn.execute(
                "SELECT colorLabels AS c, COUNT(*) AS n FROM Adobe_images "
                "WHERE colorLabels IS NOT NULL AND colorLabels != '' "
                "GROUP BY colorLabels ORDER BY n DESC LIMIT 12"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        labelled = sum(r["n"] for r in rows)
        if labelled:
            candidates.append({
                "kind": "color",
                "key": "color",
                "label": "Colour labels",
                "images": labelled,
                "share": round(labelled / total_images, 4) if total_images else 0.0,
                "strength": "medium",
                "detail": "Distribution: " + ", ".join(f"{r['c']}={r['n']:,}" for r in rows),
                "collections": [],
            })

    # 5. Star ratings.
    if _has(schema, "Adobe_images", "rating"):
        try:
            rows = conn.execute(
                "SELECT CAST(rating AS INT) AS r, COUNT(*) AS n FROM Adobe_images "
                "WHERE rating IS NOT NULL AND rating > 0 GROUP BY r ORDER BY r"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        rated = sum(r["n"] for r in rows)
        if rated:
            candidates.append({
                "kind": "rating",
                "key": "rating",
                "label": "Star ratings",
                "images": rated,
                "share": round(rated / total_images, 4) if total_images else 0.0,
                "strength": "weakest",
                "detail": ("Distribution: " + ", ".join(f"{r['r']}★={r['n']:,}" for r in rows)
                           + ". A rating is an opinion recorded during the cull; it may never "
                             "have translated into delivery. Validate it against a stronger "
                             "source on a few weddings before trusting it broadly."),
                "collections": [],
            })

    return {
        "total_images": total_images,
        "candidates": candidates,
        "has_develop_history": _has(schema, "Adobe_libraryImageDevelopHistoryStep", "image"),
        "develop_history_steps": (
            _count(conn, "SELECT COUNT(*) FROM Adobe_libraryImageDevelopHistoryStep")
            if _has(schema, "Adobe_libraryImageDevelopHistoryStep", "image") else 0
        ),
    }


# --- per-image extraction -------------------------------------------------


def extract_images(
    conn: sqlite3.Connection,
    folder_filter: str | None = None,
    collection_filter: str | None = None,
) -> list[dict]:
    """One row per image in the catalog, with every available decision field.

    ``folder_filter`` is a case-insensitive substring matched against the
    image's folder path, which is how a single wedding is pulled out of a
    catalog holding many.
    """
    schema = table_columns(conn)
    if not _has(schema, "Adobe_images", "id_local"):
        return []

    img_cols = schema["Adobe_images"]
    select_bits = ["ai.id_local AS image_id"]
    for col in ("rating", "colorLabels", "pick", "captureTime", "fileFormat", "touchTime"):
        if col in img_cols:
            select_bits.append(f"ai.{col} AS {col}")

    joins = ""
    if _has(schema, "AgLibraryFile", "id_local") and "rootFile" in img_cols:
        joins += " LEFT JOIN AgLibraryFile lf ON lf.id_local = ai.rootFile"
        for col in ("baseName", "extension", "originalFilename"):
            if col in schema["AgLibraryFile"]:
                select_bits.append(f"lf.{col} AS {col}")
        if _has(schema, "AgLibraryFolder", "id_local") and "folder" in schema["AgLibraryFile"]:
            joins += " LEFT JOIN AgLibraryFolder fo ON fo.id_local = lf.folder"
            if "pathFromRoot" in schema["AgLibraryFolder"]:
                select_bits.append("fo.pathFromRoot AS pathFromRoot")
            if _has(schema, "AgLibraryRootFolder", "id_local", "absolutePath") and \
               "rootFolder" in schema["AgLibraryFolder"]:
                joins += " LEFT JOIN AgLibraryRootFolder rf ON rf.id_local = fo.rootFolder"
                select_bits.append("rf.absolutePath AS absolutePath")

    sql = f"SELECT {', '.join(select_bits)} FROM Adobe_images ai{joins}"
    try:
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
    except sqlite3.Error:
        return []

    # Collection membership per image.
    memberships: dict[int, list[str]] = {}
    if _has(schema, "AgLibraryCollectionImage", "collection", "image") and \
       _has(schema, "AgLibraryCollection", "id_local", "name"):
        try:
            for r in conn.execute(
                "SELECT ci.image AS image, c.name AS name FROM AgLibraryCollectionImage ci "
                "JOIN AgLibraryCollection c ON c.id_local = ci.collection"
            ):
                memberships.setdefault(r["image"], []).append(r["name"] or "")
        except sqlite3.Error:
            pass

    published: set[int] = set()
    if _has(schema, "AgRemotePhoto", "photo"):
        try:
            published = {r[0] for r in conn.execute("SELECT DISTINCT photo FROM AgRemotePhoto")}
        except sqlite3.Error:
            pass

    out: list[dict] = []
    needle = folder_filter.lower() if folder_filter else None
    coll_needle = collection_filter.lower() if collection_filter else None

    for row in rows:
        folder = f"{row.get('absolutePath') or ''}{row.get('pathFromRoot') or ''}"
        if needle and needle not in folder.lower():
            continue

        colls = memberships.get(row["image_id"], [])
        if coll_needle and not any(coll_needle in c.lower() for c in colls):
            continue

        base = row.get("baseName") or ""
        out.append({
            "image_id": row["image_id"],
            "stem": base,
            "extension": row.get("extension") or "",
            "original_filename": row.get("originalFilename") or "",
            "folder": folder,
            "capture_time": row.get("captureTime") or "",
            "rating": int(row["rating"]) if row.get("rating") not in (None, "") else None,
            "color_label": row.get("colorLabels") or "",
            "pick": float(row["pick"]) if row.get("pick") not in (None, "") else None,
            "published": row["image_id"] in published,
            "collections": "|".join(sorted(set(c for c in colls if c))),
        })
    return out


def delivered_stems(rows: list[dict], source: str, threshold: int = 3,
                    collection: str | None = None) -> set[str]:
    """Reduce extracted catalog rows to the set of stems counted as delivered.

    ``source`` is one of published / pick / color / rating / collection.
    """
    chosen: set[str] = set()
    for row in rows:
        stem = (row.get("stem") or "").strip().lower()
        if not stem:
            continue
        if source == "published" and row.get("published"):
            chosen.add(stem)
        elif source == "pick" and (row.get("pick") or 0) > 0:
            chosen.add(stem)
        elif source == "color" and row.get("color_label"):
            chosen.add(stem)
        elif source == "rating" and (row.get("rating") or 0) >= threshold:
            chosen.add(stem)
        elif source == "collection" and collection:
            colls = (row.get("collections") or "").lower()
            if collection.lower() in colls:
                chosen.add(stem)
    return chosen


def write_extract(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["image_id", "stem", "extension", "original_filename", "folder",
               "capture_time", "rating", "color_label", "pick", "published", "collections"]
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_extract(path: Path) -> list[dict]:
    """Load a CSV written by ``write_extract``, restoring types."""
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append({
                "image_id": raw.get("image_id"),
                "stem": raw.get("stem", ""),
                "extension": raw.get("extension", ""),
                "original_filename": raw.get("original_filename", ""),
                "folder": raw.get("folder", ""),
                "capture_time": raw.get("capture_time", ""),
                "rating": int(raw["rating"]) if raw.get("rating") else None,
                "color_label": raw.get("color_label", ""),
                "pick": float(raw["pick"]) if raw.get("pick") else None,
                "published": str(raw.get("published", "")).lower() == "true",
                "collections": raw.get("collections", ""),
            })
    return rows


def folder_summary(rows: list[dict], limit: int = 40) -> list[dict]:
    """Image counts per folder, so a wedding can be located inside a catalog."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["folder"]] = counts.get(row["folder"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [{"folder": f, "images": n} for f, n in ordered]


# --- top-level report -----------------------------------------------------


def inspect_catalog(lrcat: Path, folder_filter: str | None = None) -> dict:
    """Report what a catalog contains, without modifying it."""
    result: dict = {"catalog": str(lrcat), "readable": False, "tables": {}, "findings": []}

    if not lrcat.exists():
        result["error"] = "catalog file not found"
        return result

    try:
        with open_catalog(lrcat) as conn:
            schema = table_columns(conn)
            result["readable"] = True
            result["table_count"] = len(schema)

            for table, wanted in INTERESTING.items():
                if table not in schema:
                    result["tables"][table] = {"present": False}
                    continue
                entry: dict = {
                    "present": True,
                    "columns_found": [c for c in wanted if c in schema[table]],
                    "rows": _count(conn, f'SELECT COUNT(*) FROM "{table}"'),
                }
                result["tables"][table] = entry

            result["labels"] = discover_label_sources(conn)

            if folder_filter:
                rows = extract_images(conn, folder_filter=folder_filter)
                result["folder_filter"] = folder_filter
                result["folder_matches"] = len(rows)
            else:
                rows = extract_images(conn)
            result["folders"] = folder_summary(rows)

            best = result["labels"]["candidates"][0] if result["labels"]["candidates"] else None
            if best:
                result["findings"].append(
                    f"Strongest available label: {best['label']} "
                    f"({best['images']:,} images). Use `--label-source {best['key']}`."
                )
            else:
                result["findings"].append(
                    "No usable decision signal found in this catalog. Check that it is the "
                    "working catalog rather than an empty or freshly created one."
                )

            if result["labels"]["develop_history_steps"]:
                result["findings"].append(
                    f"{result['labels']['develop_history_steps']:,} develop-history steps are "
                    "stored, each with a timestamp, so the order edits were applied in is "
                    "partially reconstructable."
                )
            if result["tables"].get("Adobe_imageDevelopSettings", {}).get("present"):
                result["findings"].append(
                    "Develop settings are present but serialized as a Lua table, not XML. "
                    "Read slider values from XMP sidecars instead."
                )
    except (sqlite3.Error, OSError) as exc:
        result["error"] = f"could not open catalog: {exc}"
        return result

    return result


def render_catalog_report(r: dict) -> str:
    """Console summary of a catalog inspection."""
    lines: list[str] = []
    w = lines.append

    w(f"Catalog: {r['catalog']}")
    w(f"Tables:  {r.get('table_count', 0)}")
    w("")

    labels = r.get("labels", {})
    w(f"Images in catalog: {labels.get('total_images', 0):,}")
    w("")
    w("Candidate labels, strongest first:")
    w("")
    for c in labels.get("candidates", []):
        share = f"{c['share']:.0%}" if c.get("share") else "-"
        w(f"  [{c['strength']:<9}] {c['label']}")
        w(f"              {c['images']:,} images ({share} of catalog)   --label-source {c['key']}")
        w(f"              {c['detail']}")
        for coll in (c.get("collections") or [])[:8]:
            w(f"                - {coll['name']}: {coll['images']:,}")
        if len(c.get("collections") or []) > 8:
            w(f"                - ... and {len(c['collections']) - 8} more")
        w("")

    if r.get("folders"):
        w("Largest folders (use one as --folder-filter to isolate a wedding):")
        for f in r["folders"][:15]:
            w(f"  {f['images']:>7,}  {f['folder']}")
        w("")

    for finding in r.get("findings", []):
        w(f"  * {finding}")
    return "\n".join(lines)
