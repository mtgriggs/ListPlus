"""Command-line entry point for the Phase 0 audit toolkit.

    python -m mck scan     --raw DIR --delivered DIR --out DIR
    python -m mck auto     --archive DIR --out DIR [--watch]
    python -m mck snapshot --raw DIR --out DIR --tag post-ai
    python -m mck diff     --before DIR --after DIR --out FILE
    python -m mck catalog  --lrcat FILE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .automate import RAW_DIR_NAMES, DELIVERED_DIR_NAMES, run_archive, watch
from .catalog import inspect_catalog
from .report import write_reports
from .scan import scan_wedding
from .snapshot import diff_snapshots, take_snapshot


def _paths(values: list[str] | None) -> list[Path]:
    return [Path(v).expanduser() for v in (values or [])]


def cmd_scan(args) -> int:
    raw_roots = _paths(args.raw)
    delivered_roots = _paths(args.delivered)

    missing = [p for p in raw_roots if not p.exists()]
    if missing:
        print(f"error: raw path(s) not found: {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 2
    if not raw_roots:
        print("error: --raw is required", file=sys.stderr)
        return 2

    title = args.title or raw_roots[0].parent.name or raw_roots[0].name
    print(f"Scanning {title} ...")

    records, meta = scan_wedding(
        raw_roots=raw_roots,
        delivered_roots=delivered_roots,
        burst_gap=args.burst_gap,
        scene_gap=args.scene_gap,
        prefer_exiftool=not args.no_exiftool,
        limit=args.limit,
    )
    if not records:
        print("error: no image files found under the given --raw path(s)", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser()
    payload = write_reports(records, meta, out_dir, title=title)

    a = payload["analysis"]
    print()
    print(f"  frames            {a['counts']['source_images']:,}")
    print(f"  delivered         {a['counts']['delivered_matched']:,} "
          f"({a['counts']['keep_rate']:.1%})")
    print(f"  capture time      {a['counts']['capture_time_coverage']:.0%} coverage")
    print(f"  sidecars          {a['counts']['sidecar_coverage']:.0%} coverage")
    print(f"  preference pairs  {a['bursts'].get('preference_pairs', 0):,}")
    print()
    for f in payload["findings"]:
        if f["status"] in ("FAIL", "WARN"):
            print(f"  [{f['status']}] {f['title']}")
    print()
    print(f"Report: {out_dir / 'AUDIT_REPORT.md'}")
    return 0


def cmd_auto(args) -> int:
    archive = Path(args.archive).expanduser()
    out_dir = Path(args.out).expanduser()
    if not archive.exists():
        print(f"error: archive not found: {archive}", file=sys.stderr)
        return 2

    kwargs = dict(
        force=args.force,
        limit_weddings=args.limit_weddings,
        limit_images=args.limit,
        prefer_exiftool=not args.no_exiftool,
        raw_names=args.raw_names.split(",") if args.raw_names else None,
        delivered_names=args.delivered_names.split(",") if args.delivered_names else None,
    )
    if args.watch:
        watch(archive, out_dir, interval=args.interval, **kwargs)
        return 0
    run_archive(archive, out_dir, **kwargs)
    return 0


def cmd_snapshot(args) -> int:
    roots = _paths(args.raw)
    if not roots:
        print("error: --raw is required", file=sys.stderr)
        return 2
    result = take_snapshot(
        roots=roots,
        out_dir=Path(args.out).expanduser(),
        tag=args.tag,
        copy_files=not args.no_copy,
    )
    print(f"Snapshot '{args.tag}': {result['count']:,} sidecars -> {result['dir']}")
    if result["count"] == 0:
        print("warning: no .xmp sidecars found. In Lightroom, select all and press "
              "Ctrl/Cmd+S to write sidecars before snapshotting.", file=sys.stderr)
    return 0


def cmd_diff(args) -> int:
    before = Path(args.before).expanduser()
    after = Path(args.after).expanduser()
    for p in (before, after):
        if not p.exists():
            print(f"error: snapshot not found: {p}", file=sys.stderr)
            return 2

    out_path = Path(args.out).expanduser() if args.out else None
    result = diff_snapshots(before, after, out_path)

    print(f"  {result['before']['tag']} -> {result['after']['tag']}")
    print(f"  unchanged   {result['unchanged']:,}")
    print(f"  changed     {result['changed']:,} ({result['disagreement_rate']:.1%})")
    print(f"  promoted    {result['promoted']:,}")
    print(f"  demoted     {result['demoted']:,}")
    if out_path:
        print(f"  written to  {out_path}")
    return 0


def cmd_catalog(args) -> int:
    result = inspect_catalog(Path(args.lrcat).expanduser())
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("readable") else 1

    if not result.get("readable"):
        print(f"error: {result.get('error', 'catalog unreadable')}", file=sys.stderr)
        print("Make sure Lightroom is closed and the path points at the .lrcat file.",
              file=sys.stderr)
        return 1

    print(f"Catalog: {result['catalog']}")
    print(f"Tables:  {result['table_count']}")
    print()
    for table, info in result["tables"].items():
        if info.get("present"):
            rows = info.get("rows")
            print(f"  {table:<45} {rows if rows is not None else '?':>12} rows")
        else:
            print(f"  {table:<45} {'absent':>12}")
    for key in ("pick_distribution", "rating_distribution", "color_label_distribution"):
        if key in result:
            print()
            print(f"  {key.replace('_', ' ')}: {result[key]}")
    if result["findings"]:
        print()
        for finding in result["findings"]:
            print(f"  * {finding}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mck",
        description="McKinley AI Phase 0 — read-only audit of a Lightroom/Aftershoot archive.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="audit one wedding")
    s.add_argument("--raw", action="append", required=True,
                   help="folder of source frames (repeatable)")
    s.add_argument("--delivered", action="append",
                   help="folder of final delivered JPEGs (repeatable)")
    s.add_argument("--out", default="./audit-out", help="output directory")
    s.add_argument("--title", help="name for the report")
    s.add_argument("--burst-gap", type=float, default=2.0,
                   help="seconds between frames within a burst (default 2.0)")
    s.add_argument("--scene-gap", type=float, default=420.0,
                   help="seconds of inactivity that starts a new scene (default 420)")
    s.add_argument("--limit", type=int, help="only process the first N frames")
    s.add_argument("--no-exiftool", action="store_true",
                   help="force the built-in EXIF reader")
    s.set_defaults(func=cmd_scan)

    a = sub.add_parser("auto", help="audit every wedding under an archive root")
    a.add_argument("--archive", required=True, help="root folder containing wedding folders")
    a.add_argument("--out", default="./audit-out", help="output directory")
    a.add_argument("--force", action="store_true", help="re-audit unchanged weddings")
    a.add_argument("--watch", action="store_true", help="keep running and pick up new weddings")
    a.add_argument("--interval", type=int, default=300, help="watch poll seconds (default 300)")
    a.add_argument("--limit-weddings", type=int, help="only process the first N weddings")
    a.add_argument("--limit", type=int, help="only process the first N frames per wedding")
    a.add_argument("--raw-names", help=f"comma-separated raw folder names (default: {','.join(RAW_DIR_NAMES)})")
    a.add_argument("--delivered-names",
                   help=f"comma-separated delivered folder names (default: {','.join(DELIVERED_DIR_NAMES)})")
    a.add_argument("--no-exiftool", action="store_true")
    a.set_defaults(func=cmd_auto)

    n = sub.add_parser("snapshot", help="freeze current sidecar state")
    n.add_argument("--raw", action="append", required=True, help="folder to snapshot (repeatable)")
    n.add_argument("--out", default="./snapshots", help="snapshot directory")
    n.add_argument("--tag", required=True, help="stage name, e.g. post-ai or post-review")
    n.add_argument("--no-copy", action="store_true", help="record state without copying files")
    n.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("diff", help="compare two snapshots into a disagreement set")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--out", help="output .json or .jsonl")
    d.set_defaults(func=cmd_diff)

    c = sub.add_parser("catalog", help="inspect a Lightroom .lrcat")
    c.add_argument("--lrcat", required=True)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_catalog)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
