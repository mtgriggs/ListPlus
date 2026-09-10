"""One-shot reconnaissance: everything needed to plan the audit, in one file.

Phase 0 has a chicken-and-egg problem. Choosing which wedding to audit, and
which catalog signal to use as the label, both require knowing what the archive
and the catalogs actually contain. Asking for those one command at a time costs
a round trip each.

This runs the whole reconnaissance pass unattended and writes a single report:
which drives are attached, what folder layout each archive uses, which Lightroom
catalogs exist, and which candidate labels each catalog holds. That file is the
handoff.

Nothing is modified. Catalogs are copied before opening and opened read-only.

**Privacy note:** the report contains folder and catalog names, which for a
wedding archive means client names. It contains no images and no email
addresses. Read it before sending it anywhere.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .automate import render_survey, survey_archive
from .catalog import inspect_catalog

# Where Lightroom catalogs usually live. Searched depth-first with a low limit,
# because a full walk of a multi-terabyte drive is not worth it to find a file
# that lives near the top of a known folder.
CATALOG_SEARCH_ROOTS = [
    "~/Pictures", "~/Documents", "~/Desktop", "~/Lightroom", "~/Movies",
]
CATALOG_SEARCH_DEPTH = 4
MAX_CATALOGS = 25


def find_catalogs(
    extra_roots: list[Path] | None = None,
    depth: int = CATALOG_SEARCH_DEPTH,
    limit: int = MAX_CATALOGS,
) -> list[dict]:
    """Locate .lrcat files without walking entire drives."""
    roots = [Path(r).expanduser() for r in CATALOG_SEARCH_ROOTS]
    roots += list(extra_roots or [])

    found: dict[Path, dict] = {}

    def walk(directory: Path, remaining: int) -> None:
        if remaining < 0 or len(found) >= limit:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if len(found) >= limit:
                return
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_file() and name.lower().endswith(".lrcat"):
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                found[entry] = {
                    "path": str(entry),
                    "size_mb": round(stat.st_size / (1024 * 1024), 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
                    # Lightroom writes dated copies into a Backups folder; those
                    # are stale duplicates, not separate catalogs.
                    "is_backup": "backup" in str(entry.parent).lower(),
                }
            elif entry.is_dir():
                walk(entry, remaining - 1)

    for root in roots:
        if root.exists():
            walk(root, depth)

    rows = sorted(found.values(), key=lambda r: (r["is_backup"], -r["size_mb"]))
    return rows


def detect_volumes() -> list[Path]:
    """External drives currently attached, excluding the boot volume."""
    volumes = Path("/Volumes")
    if not volumes.exists():
        return []
    out = []
    try:
        for entry in sorted(volumes.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                # The boot drive appears here as a symlink to /.
                if entry.is_symlink():
                    continue
                out.append(entry)
    except OSError:
        pass
    return out


def run(
    out_dir: Path,
    archives: list[Path] | None = None,
    catalogs: list[Path] | None = None,
    skip_catalog_search: bool = False,
    log=print,
) -> dict:
    """Survey archives and catalogs, write one handoff report."""
    out_dir.mkdir(parents=True, exist_ok=True)

    archives = archives or detect_volumes()
    log(f"Archives to survey: {', '.join(str(a) for a in archives) or 'none found'}")

    surveys = []
    for archive in archives:
        log(f"  surveying {archive} ...")
        try:
            surveys.append(survey_archive(archive))
        except Exception as exc:  # noqa: BLE001 - one bad drive must not stop the pass
            surveys.append({"archive": str(archive), "exists": False,
                            "error": str(exc), "candidates": [],
                            "subfolder_names": {}, "matched": 0, "unmatched": 0})
            log(f"    failed: {exc}")

    if catalogs:
        catalog_files = [{"path": str(c), "size_mb": 0.0, "modified": "",
                          "is_backup": False} for c in catalogs]
    elif skip_catalog_search:
        catalog_files = []
    else:
        log("  searching for Lightroom catalogs ...")
        catalog_files = find_catalogs(extra_roots=archives)
        log(f"    found {len(catalog_files)}")

    inspections = []
    for entry in catalog_files:
        if entry["is_backup"]:
            continue
        log(f"  reading {Path(entry['path']).name} ...")
        try:
            report = inspect_catalog(Path(entry["path"]))
        except Exception as exc:  # noqa: BLE001
            report = {"catalog": entry["path"], "readable": False, "error": str(exc)}
        report["file"] = entry
        inspections.append(report)
        if not report.get("readable"):
            log(f"    unreadable: {report.get('error', 'unknown')}")

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "archives": surveys,
        "catalog_files": catalog_files,
        "catalogs": inspections,
    }
    (out_dir / "handoff.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "HANDOFF.md").write_text(render(payload), encoding="utf-8")
    return payload


def render(p: dict) -> str:
    from .catalog import render_catalog_report

    lines: list[str] = []
    w = lines.append
    w("# McKinley AI — Phase 0 reconnaissance")
    w("")
    w(f"_Generated {p['generated']}_")
    w("")
    w("Read-only. No photograph, sidecar or catalog was modified.")
    w("")
    w("> Contains folder and catalog names, which for a wedding archive means "
      "client names. No images, no contact details.")
    w("")

    w("## Archives")
    w("")
    if not p["archives"]:
        w("No drives found. Attach the archive drive and re-run, or pass "
          "`--archive /path` explicitly.")
        w("")
    for s in p["archives"]:
        w("```")
        w(render_survey(s))
        w("```")
        w("")

    w("## Lightroom catalogs")
    w("")
    if not p["catalog_files"]:
        w("None found in the usual locations. Pass `--catalog /path/to/File.lrcat` "
          "to point at one directly.")
        w("")
    else:
        w("| Catalog | Size | Last modified | Backup copy |")
        w("| --- | --- | --- | --- |")
        for c in p["catalog_files"]:
            w(f"| `{c['path']}` | {c['size_mb']:,.1f} MB | {c['modified']} | "
              f"{'yes' if c['is_backup'] else 'no'} |")
        w("")
        w("Backup copies are the dated duplicates Lightroom writes into a "
          "`Backups` folder. They are skipped below.")
        w("")

    for report in p["catalogs"]:
        name = Path(report["catalog"]).name
        w(f"### {name}")
        w("")
        if not report.get("readable"):
            w(f"Could not read: {report.get('error', 'unknown error')}")
            w("")
            w("If this says the database is locked, close Lightroom and re-run.")
            w("")
            continue
        w("```")
        w(render_catalog_report(report))
        w("```")
        w("")

    w("---")
    w("")
    w("## What this decides")
    w("")
    w("- Which folder-name flags the archive run needs (`--raw-names`).")
    w("- Which catalog signal becomes the keep/drop label (`--label-source`).")
    w("- Which wedding to audit first.")
    return "\n".join(lines)
