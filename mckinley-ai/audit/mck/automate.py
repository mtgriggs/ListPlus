"""The automator — run the audit across a whole archive, unattended.

Auditing one wedding by hand answers "is this viable". Auditing a hundred
answers the questions that actually determine whether the project is worth
building:

* Is the keep rate stable across years, or did the style drift? A model trained
  on a moving target needs recency weighting.
* How many preference pairs exist in total? That is the real dataset size, and
  it is far larger than the image count.
* Which weddings are unusable (missing delivered sets, broken joins) and should
  be excluded before they poison the training set?

Runs incrementally against a ledger, so re-running after adding weddings only
processes what changed. ``--watch`` keeps it running so newly finished weddings
are absorbed without being asked.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from .report import write_reports
from .scan import scan_wedding

# Folder names commonly used for each side of the join, matched case-insensitively.
RAW_DIR_NAMES = [
    "raw", "raws", "originals", "original", "source", "capture", "cards", "import",
]
DELIVERED_DIR_NAMES = [
    "delivered", "final", "finals", "gallery", "export", "exports", "edited",
    "jpegs", "jpgs", "client", "delivery", "output",
]


def _find_child(parent: Path, names: list[str]) -> list[Path]:
    """Immediate subdirectories whose name matches one of ``names``."""
    hits: list[Path] = []
    try:
        for entry in parent.iterdir():
            if entry.is_dir() and entry.name.lower() in names:
                hits.append(entry)
    except OSError:
        pass
    return sorted(hits)


def discover_weddings(
    archive: Path,
    raw_names: list[str] | None = None,
    delivered_names: list[str] | None = None,
) -> list[dict]:
    """Find wedding folders under an archive root.

    A wedding is an immediate child directory of the archive that contains a
    recognisable raw folder, a recognisable delivered folder, or both. When
    neither is present the directory itself is treated as the raw root, which
    covers flat layouts.
    """
    raw_names = raw_names or RAW_DIR_NAMES
    delivered_names = delivered_names or DELIVERED_DIR_NAMES

    weddings: list[dict] = []
    if not archive.exists():
        return weddings

    for child in sorted(archive.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        raw_dirs = _find_child(child, raw_names)
        delivered_dirs = _find_child(child, delivered_names)

        if not raw_dirs and not delivered_dirs:
            # Flat layout: no recognisable structure, skip rather than guess.
            continue
        if not raw_dirs:
            raw_dirs = [child]

        weddings.append({
            "name": child.name,
            "root": child,
            "raw": raw_dirs,
            "delivered": delivered_dirs,
        })
    return weddings


def _fingerprint(wedding: dict) -> str:
    """Cheap change detector: file counts and newest mtime per side."""
    parts = []
    for key in ("raw", "delivered"):
        count = 0
        newest = 0.0
        for root in wedding.get(key, []):
            for path in Path(root).rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    count += 1
                    try:
                        newest = max(newest, path.stat().st_mtime)
                    except OSError:
                        pass
        parts.append(f"{key}:{count}:{int(newest)}")
    return "|".join(parts)


def _load_ledger(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "weddings": {}}


def _save_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")


def run_archive(
    archive: Path,
    out_dir: Path,
    force: bool = False,
    limit_weddings: int | None = None,
    limit_images: int | None = None,
    prefer_exiftool: bool = True,
    raw_names: list[str] | None = None,
    delivered_names: list[str] | None = None,
    log=print,
) -> dict:
    """Audit every wedding under ``archive``, skipping unchanged ones."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger.json"
    ledger = _load_ledger(ledger_path)

    weddings = discover_weddings(archive, raw_names, delivered_names)
    if limit_weddings:
        weddings = weddings[:limit_weddings]

    log(f"Discovered {len(weddings)} wedding folder(s) under {archive}")
    processed, skipped, failed = 0, 0, 0

    for wedding in weddings:
        name = wedding["name"]
        entry = ledger["weddings"].get(name, {})
        fp = _fingerprint(wedding)

        if not force and entry.get("fingerprint") == fp and entry.get("status") == "ok":
            skipped += 1
            continue

        log(f"  auditing {name} ...")
        wedding_out = out_dir / "weddings" / name
        try:
            records, meta = scan_wedding(
                raw_roots=[Path(p) for p in wedding["raw"]],
                delivered_roots=[Path(p) for p in wedding["delivered"]],
                prefer_exiftool=prefer_exiftool,
                limit=limit_images,
            )
            payload = write_reports(records, meta, wedding_out, title=name)
            a = payload["analysis"]
            ledger["weddings"][name] = {
                "status": "ok",
                "fingerprint": fp,
                "audited_at": datetime.now().isoformat(timespec="seconds"),
                "out_dir": str(wedding_out),
                "source_images": a["counts"]["source_images"],
                "delivered": a["counts"]["delivered_matched"],
                "keep_rate": a["counts"]["keep_rate"],
                "capture_time_coverage": a["counts"]["capture_time_coverage"],
                "preference_pairs": a["bursts"].get("preference_pairs", 0),
                "rating_auc": a["labels"]["rating_auc"],
                "near_dupe_share": a["duplicate_work"]["share_of_drops_that_are_near_dupes"],
                "first_frame": a["timeline"]["first_frame"],
                "fails": [f["title"] for f in payload["findings"] if f["status"] == "FAIL"],
            }
            processed += 1
            log(f"    {a['counts']['source_images']:,} frames, "
                f"keep {a['counts']['keep_rate']:.1%}, "
                f"{a['bursts'].get('preference_pairs', 0):,} pairs")
        except Exception as exc:  # noqa: BLE001 - one bad wedding must not stop the run
            failed += 1
            ledger["weddings"][name] = {
                "status": "error",
                "fingerprint": fp,
                "audited_at": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc),
                "traceback": traceback.format_exc(limit=5),
            }
            log(f"    FAILED: {exc}")

        _save_ledger(ledger_path, ledger)

    rollup = build_rollup(ledger)
    (out_dir / "ROLLUP.md").write_text(render_rollup(rollup), encoding="utf-8")
    (out_dir / "rollup.json").write_text(
        json.dumps(rollup, indent=2, default=str), encoding="utf-8"
    )
    _save_ledger(ledger_path, ledger)

    log(f"Done: {processed} audited, {skipped} unchanged, {failed} failed")
    log(f"Roll-up: {out_dir / 'ROLLUP.md'}")
    return {"processed": processed, "skipped": skipped, "failed": failed, "rollup": rollup}


def build_rollup(ledger: dict) -> dict:
    """Aggregate ledger entries into archive-level statistics."""
    ok = [v for v in ledger["weddings"].values() if v.get("status") == "ok"]
    errored = [k for k, v in ledger["weddings"].items() if v.get("status") == "error"]

    total_images = sum(v.get("source_images", 0) for v in ok)
    total_delivered = sum(v.get("delivered", 0) for v in ok)
    total_pairs = sum(v.get("preference_pairs", 0) for v in ok)
    keep_rates = [v["keep_rate"] for v in ok if v.get("keep_rate")]
    aucs = [v["rating_auc"] for v in ok if v.get("rating_auc") is not None]

    usable = [v for v in ok if not v.get("fails")]
    unusable = [
        {"name": k, "reasons": v.get("fails", [])}
        for k, v in ledger["weddings"].items()
        if v.get("status") == "ok" and v.get("fails")
    ]

    by_year: dict[str, dict] = {}
    for v in ok:
        year = (v.get("first_frame") or "n/a")[:4]
        bucket = by_year.setdefault(year, {"weddings": 0, "images": 0, "delivered": 0})
        bucket["weddings"] += 1
        bucket["images"] += v.get("source_images", 0)
        bucket["delivered"] += v.get("delivered", 0)
    for bucket in by_year.values():
        bucket["keep_rate"] = (
            round(bucket["delivered"] / bucket["images"], 4) if bucket["images"] else 0.0
        )

    mean_keep = sum(keep_rates) / len(keep_rates) if keep_rates else 0.0
    spread = (max(keep_rates) - min(keep_rates)) if len(keep_rates) > 1 else 0.0

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "weddings_audited": len(ok),
        "weddings_usable": len(usable),
        "weddings_errored": errored,
        "weddings_unusable": unusable,
        "total_source_images": total_images,
        "total_delivered": total_delivered,
        "overall_keep_rate": round(total_delivered / total_images, 4) if total_images else 0.0,
        "mean_wedding_keep_rate": round(mean_keep, 4),
        "keep_rate_spread": round(spread, 4),
        "total_preference_pairs": total_pairs,
        "mean_rating_auc": round(sum(aucs) / len(aucs), 4) if aucs else None,
        "by_year": dict(sorted(by_year.items())),
    }


def render_rollup(r: dict) -> str:
    lines: list[str] = []
    w = lines.append
    w("# McKinley AI — Archive Roll-up")
    w("")
    w(f"_Generated {r['generated']}_")
    w("")
    w("## Dataset scale")
    w("")
    w("| Metric | Value |")
    w("| --- | --- |")
    w(f"| Weddings audited | {r['weddings_audited']:,} |")
    w(f"| Weddings usable | {r['weddings_usable']:,} |")
    w(f"| Source frames | {r['total_source_images']:,} |")
    w(f"| Delivered frames | {r['total_delivered']:,} |")
    w(f"| Overall keep rate | {r['overall_keep_rate']:.1%} |")
    w(f"| **Within-burst preference pairs** | **{r['total_preference_pairs']:,}** |")
    w(f"| Mean star-rating AUC | {r['mean_rating_auc'] if r['mean_rating_auc'] is not None else 'n/a'} |")
    w("")

    w("## Consistency over time")
    w("")
    w(f"Mean per-wedding keep rate **{r['mean_wedding_keep_rate']:.1%}**, "
      f"spread **{r['keep_rate_spread']:.1%}**.")
    w("")
    if r["keep_rate_spread"] > 0.25:
        w("> ⚠️ Keep rate varies widely between weddings. Some of that is the events "
          "themselves, but a large spread also means your selectivity is not a fixed "
          "target. Weight recent weddings more heavily and re-check whether older years "
          "still represent how you cull today.")
        w("")
    if r["by_year"]:
        w("| Year | Weddings | Frames | Delivered | Keep rate |")
        w("| --- | --- | --- | --- | --- |")
        for year, b in r["by_year"].items():
            w(f"| {year} | {b['weddings']:,} | {b['images']:,} | "
              f"{b['delivered']:,} | {b['keep_rate']:.1%} |")
        w("")

    if r["weddings_unusable"]:
        w("## Excluded from training")
        w("")
        for item in r["weddings_unusable"]:
            w(f"- **{item['name']}** — {'; '.join(item['reasons'])}")
        w("")
    if r["weddings_errored"]:
        w("## Errored")
        w("")
        for name in r["weddings_errored"]:
            w(f"- {name}")
        w("")
    return "\n".join(lines)


def watch(
    archive: Path,
    out_dir: Path,
    interval: int = 300,
    log=print,
    **kwargs,
) -> None:
    """Poll the archive and audit anything new or changed."""
    log(f"Watching {archive} every {interval}s. Ctrl-C to stop.")
    try:
        while True:
            run_archive(archive, out_dir, log=log, **kwargs)
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Stopped.")
