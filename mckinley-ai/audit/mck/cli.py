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
from .cullwatch import run_intake, watch_intake
from .editorial import (
    PROFILES, SubmissionLedger, evaluate, load_wedding_meta, meta_template,
    render_submission,
)
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


def cmd_cull(args) -> int:
    intake = Path(args.intake).expanduser()
    out_dir = Path(args.out).expanduser()
    if not intake.exists():
        print(f"error: intake folder not found: {intake}", file=sys.stderr)
        return 2

    if args.write:
        print("NOTE: --write will modify XMP sidecars. Existing sidecars are backed up")
        print(f"      to a timestamped folder under {out_dir} before any change.")
        print()

    kwargs = dict(
        settle_seconds=args.settle,
        keep_rate=args.keep_rate,
        write_xmp=args.write,
        prefer_exiftool=not args.no_exiftool,
    )
    if args.watch:
        watch_intake(intake, out_dir, interval=args.interval, **kwargs)
        return 0
    run_intake(intake, out_dir, **kwargs)
    return 0


def cmd_editorial(args) -> int:
    out_dir = Path(args.out).expanduser()
    ledger = SubmissionLedger(out_dir / "submissions.json")

    if args.action == "profiles":
        for profile in PROFILES.values():
            print(f"  {profile.key:<18} {profile.name}")
            print(f"  {'':<18} {profile.min_images}–{profile.max_images} images"
                  + (f", min short edge {profile.min_short_edge}px" if profile.min_short_edge else "")
                  + (f", max {profile.max_file_mb:.0f} MB" if profile.max_file_mb else ""))
            if profile.source:
                print(f"  {'':<18} {profile.source}")
            print()
        return 0

    if not args.wedding:
        print("error: --wedding is required", file=sys.stderr)
        return 2

    meta_path = out_dir / "weddings" / f"{args.wedding}.json"

    if args.action == "init":
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        if meta_path.exists() and not args.force:
            print(f"error: {meta_path} already exists (use --force to overwrite)",
                  file=sys.stderr)
            return 2
        meta_path.write_text(
            json.dumps(meta_template(args.wedding), indent=2), encoding="utf-8"
        )
        print(f"Created {meta_path}")
        print("Fill in the vendor credits and tick off the detail categories, then run:")
        print(f"  python3 -m mck editorial check --wedding {args.wedding} "
              f"--delivered /path/Delivered")
        return 0

    if args.action == "status":
        if not args.publication or not args.set:
            print("error: status needs --publication and --set", file=sys.stderr)
            return 2
        if ledger.set_status(args.wedding, args.publication, args.set):
            print(f"{args.wedding} @ {args.publication} -> {args.set}")
            return 0
        print(f"error: no submission of {args.wedding} to {args.publication} on record",
              file=sys.stderr)
        return 1

    profile = PROFILES.get(args.publication or "generic")
    if profile is None:
        print(f"error: unknown publication {args.publication!r}. "
              f"Known: {', '.join(PROFILES)}", file=sys.stderr)
        return 2

    if args.action == "submit":
        entry = ledger.record(args.wedding, profile.key, response_days=profile.response_days)
        print(f"Recorded: {args.wedding} submitted to {profile.name} on {entry['submitted']}.")
        print(f"Response window: {profile.response_days} days. Exclusivity now applies —"
              " other outlets will be blocked until this resolves.")
        return 0

    # action == "check"
    delivered = _paths(args.delivered)
    if not delivered:
        print("error: --delivered is required", file=sys.stderr)
        return 2

    meta = load_wedding_meta(meta_path)
    if not meta:
        print(f"note: no wedding metadata at {meta_path}.")
        print(f"      Run: python3 -m mck editorial init --wedding {args.wedding}")
        print()

    report = evaluate(
        wedding=args.wedding,
        delivered_roots=delivered,
        profile=profile,
        ledger=ledger,
        meta=meta,
        prefer_exiftool=not args.no_exiftool,
    )

    dest = out_dir / "weddings" / args.wedding
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"SUBMISSION-{profile.key}.md").write_text(
        render_submission(report), encoding="utf-8"
    )
    (dest / f"submission-{profile.key}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    c = report["counts"]
    print(f"{report['wedding']} -> {profile.name}")
    print(f"  delivered      {c['delivered']:,}")
    print(f"  meets spec     {c['spec_eligible']:,}")
    print(f"  selected       {c['selected']:,} "
          f"(needs {profile.min_images}–{profile.max_images})")
    print()
    for b in report["blockers"]:
        print(f"  [BLOCKER] {b}")
    for warn in report["warnings"]:
        print(f"  [WARN] {warn}")
    print()
    print(f"{'READY' if report['ready'] else 'NOT READY'} — "
          f"{dest / f'SUBMISSION-{profile.key}.md'}")
    return 0 if report["ready"] else 1


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

    k = sub.add_parser("cull", help="automator: pre-cull weddings dropped into an intake folder")
    k.add_argument("--intake", required=True, help="folder that card dumps land in")
    k.add_argument("--out", default="./cull-out", help="output directory")
    k.add_argument("--watch", action="store_true", help="keep running and pick up new drops")
    k.add_argument("--interval", type=int, default=120, help="watch poll seconds")
    k.add_argument("--settle", type=float, default=120.0,
                   help="seconds a folder must be quiet before it counts as finished copying")
    k.add_argument("--keep-rate", type=float, default=0.18,
                   help="target share of frames to propose keeping (default 0.18)")
    k.add_argument("--write", action="store_true",
                   help="write proposed ratings into XMP sidecars (backs up first). "
                        "Without this, only a proposal file is produced.")
    k.add_argument("--no-exiftool", action="store_true")
    k.set_defaults(func=cmd_cull)

    e = sub.add_parser("editorial", help="automator: prepare and track editorial submissions")
    e.add_argument("action", choices=["check", "init", "submit", "status", "profiles"],
                   help="check = build a submission package; init = create wedding metadata; "
                        "submit = record a submission; status = update an outcome; "
                        "profiles = list known publications")
    e.add_argument("--wedding", help="wedding identifier, e.g. 2025-06-14-smith")
    e.add_argument("--delivered", action="append", help="delivered gallery folder (repeatable)")
    e.add_argument("--publication", help=f"one of: {', '.join(PROFILES)}")
    e.add_argument("--out", default="./editorial", help="output directory")
    e.add_argument("--set", help="status value: pending, published, declined, withdrawn")
    e.add_argument("--force", action="store_true", help="overwrite existing wedding metadata")
    e.add_argument("--no-exiftool", action="store_true")
    e.set_defaults(func=cmd_editorial)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
