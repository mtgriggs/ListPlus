"""Tests for the Phase 0 audit toolkit.

Run: python3 -m pytest audit/tests -q
 or: python3 audit/tests/test_audit.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mck.bursts import pair_count, segment, summarize            # noqa: E402
from mck.exif import read_exif_builtin                            # noqa: E402
from mck.report import analyze, verdicts, write_reports           # noqa: E402
from mck.scan import normalize_stem, scan_wedding                 # noqa: E402
from mck.snapshot import diff_snapshots, take_snapshot            # noqa: E402
from mck.stats import auc, entropy                                # noqa: E402
from mck.xmp import guess_writer, parse_xmp                       # noqa: E402

from make_fixtures import (                                       # noqa: E402
    build_jpeg_with_exif, build_tiff_exif, build_xmp, make_archive, make_wedding,
)

FAILURES: list[str] = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print(f"  FAIL: {msg}")
    return cond


# --- EXIF -----------------------------------------------------------------

def test_exif_tiff_roundtrip(tmp: Path):
    dt = datetime(2025, 6, 14, 15, 42, 7)
    tiff = build_tiff_exif(dt, "Canon", "EOS R5", "BODY-A", "RF50mm F1.2 L USM",
                           1600, 1.8, 1 / 500, 50.0, False, subsec="42")
    p = tmp / "IMG_0001.CR2"
    p.write_bytes(tiff)

    rec = read_exif_builtin(p)
    check(rec.source == "builtin", "builtin EXIF backend should report its source")
    check(rec.capture_time is not None, "capture time must parse from a TIFF raw")
    check(rec.capture_time.startswith("2025-06-14T15:42:07"),
          f"capture time wrong: {rec.capture_time}")
    check(rec.make == "Canon", f"make wrong: {rec.make}")
    check(rec.model == "EOS R5", f"model wrong: {rec.model}")
    check(rec.body_serial == "BODY-A", f"serial wrong: {rec.body_serial}")
    check(rec.lens == "RF50mm F1.2 L USM", f"lens wrong: {rec.lens}")
    check(rec.iso == 1600, f"iso wrong: {rec.iso}")
    check(abs((rec.aperture or 0) - 1.8) < 0.01, f"aperture wrong: {rec.aperture}")
    check(abs((rec.focal_length or 0) - 50.0) < 0.01, f"focal wrong: {rec.focal_length}")
    check(rec.subsec == "42", f"subsec wrong: {rec.subsec}")
    check(rec.body_key == "BODY-A", "body_key should prefer the serial number")


def test_exif_jpeg(tmp: Path):
    dt = datetime(2025, 6, 14, 16, 0, 0)
    tiff = build_tiff_exif(dt, "Canon", "EOS R6", "BODY-B", "RF85mm", 400, 2.8, 1 / 200, 85.0, True)
    p = tmp / "delivered.jpg"
    p.write_bytes(build_jpeg_with_exif(tiff))

    rec = read_exif_builtin(p)
    check(rec.capture_time is not None, "capture time must parse from JPEG APP1")
    check(rec.capture_time.startswith("2025-06-14T16:00:00"),
          f"jpeg capture time wrong: {rec.capture_time}")
    check(rec.flash_fired is True, "flash flag should be read from JPEG EXIF")


def test_exif_missing(tmp: Path):
    p = tmp / "garbage.CR2"
    p.write_bytes(b"not a real raw file at all")
    rec = read_exif_builtin(p)
    check(rec.source == "none", "unparseable file should degrade to source=none")
    check(rec.capture_epoch is None, "unparseable file must not invent a timestamp")


# --- XMP ------------------------------------------------------------------

def test_xmp_attributes(tmp: Path):
    p = tmp / "a.xmp"
    p.write_text(build_xmp(
        rating=4, label="Green", creator="Aftershoot 2.x",
        develop={"Exposure2012": 0.35, "Highlights2012": -40.0, "Shadows2012": 25.0},
        masks=True,
    ), encoding="utf-8")

    doc = parse_xmp(p)
    check(doc.parse_error is None, f"parse error: {doc.parse_error}")
    check(doc.rating == 4, f"rating wrong: {doc.rating}")
    check(doc.label == "Green", f"label wrong: {doc.label}")
    check(doc.process_version == "11.0", f"process version wrong: {doc.process_version}")
    check(abs((doc.get_float("crs:Exposure2012") or 0) - 0.35) < 1e-6, "exposure not parsed")
    check(doc.has_develop, "has_develop should be true")
    check(doc.has_nontrivial_develop, "non-default sliders should count as an edit")
    check(doc.mask_count == 1, f"mask count wrong: {doc.mask_count}")
    check(guess_writer(doc) == "aftershoot", f"writer attribution wrong: {guess_writer(doc)}")


def test_xmp_element_form(tmp: Path):
    """Fields written as child elements, not attributes."""
    p = tmp / "b.xmp"
    p.write_text(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about=""'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
        ' xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">'
        "<xmp:Rating>5</xmp:Rating>"
        "<xmp:Label>Red</xmp:Label>"
        "<crs:Exposure2012>-0.50</crs:Exposure2012>"
        "<xmp:CreatorTool>Adobe Lightroom Classic 14.2</xmp:CreatorTool>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>',
        encoding="utf-8",
    )
    doc = parse_xmp(p)
    check(doc.rating == 5, f"element-form rating wrong: {doc.rating}")
    check(doc.label == "Red", f"element-form label wrong: {doc.label}")
    check(abs((doc.get_float("crs:Exposure2012") or 0) + 0.5) < 1e-6,
          "element-form exposure wrong")
    check(guess_writer(doc) == "lightroom", "should attribute to lightroom")


def test_xmp_zero_develop_is_not_an_edit(tmp: Path):
    p = tmp / "c.xmp"
    p.write_text(build_xmp(rating=2, label=None, creator="Adobe Lightroom Classic",
                           develop={"Exposure2012": 0.0, "Contrast2012": 0.0}),
                 encoding="utf-8")
    doc = parse_xmp(p)
    check(doc.has_develop, "zeroed sliders still count as develop data present")
    check(not doc.has_nontrivial_develop,
          "all-zero sliders must NOT count as a styling decision")


def test_xmp_malformed(tmp: Path):
    p = tmp / "bad.xmp"
    p.write_text("<not-xml at all", encoding="utf-8")
    doc = parse_xmp(p)
    check(doc.parse_error is not None, "malformed XMP should record an error, not raise")
    check(doc.rating is None, "malformed XMP must not produce a rating")


# --- stats ----------------------------------------------------------------

def test_auc():
    check(auc([1, 2, 3, 4], [False, False, True, True]) == 1.0, "perfect separation should be 1.0")
    check(auc([4, 3, 2, 1], [False, False, True, True]) == 0.0, "inverted should be 0.0")
    check(auc([1, 1, 1, 1], [False, False, True, True]) == 0.5, "all ties should be 0.5")
    check(auc([1, 2], [True, True]) is None, "single-class input should return None")


def test_entropy():
    check(entropy([10]) == 0.0, "single bucket has zero entropy")
    check(abs(entropy([1, 1]) - 1.0) < 1e-9, "two equal buckets is 1 bit")
    check(entropy([]) == 0.0, "empty input is zero")


# --- bursts ---------------------------------------------------------------

def test_burst_segmentation():
    epochs = [0.0, 0.3, 0.6, 30.0, 30.4, 100.0, None]
    bodies = ["A", "A", "A", "A", "A", "A", "A"]
    segs = segment(epochs, bodies, gap=2.0)
    check(len(segs) == 3, f"expected 3 bursts, got {len(segs)}")
    check([s.size for s in segs] == [3, 2, 1], f"burst sizes wrong: {[s.size for s in segs]}")

    kept = [True, False, False, False, False, False, False]
    check(pair_count(segs, kept) == 2, "a 3-frame burst with 1 keeper yields 2 pairs")

    s = summarize(segs, kept)
    check(s["decisive"] == 1, f"decisive count wrong: {s['decisive']}")
    check(s["wholesale_drop"] == 1, f"wholesale_drop wrong: {s['wholesale_drop']}")


def test_bursts_split_by_body():
    """Two photographers shooting at the same instant are separate bursts."""
    epochs = [0.0, 0.1, 0.2, 0.3]
    bodies = ["A", "B", "A", "B"]
    segs = segment(epochs, bodies, gap=2.0)
    check(len(segs) == 2, f"expected one burst per body, got {len(segs)}")
    check(all(s.size == 2 for s in segs), "each body should own 2 frames")


def test_scenes_span_bodies():
    """Scenes must merge cameras; bursts must not."""
    epochs = [0.0, 1.0, 2.0, 3.0]
    bodies = ["A", "B", "A", "B"]

    bursts = segment(epochs, bodies, gap=2.0, per_body=True)
    check(len(bursts) == 2, f"bursts stay per-body, got {len(bursts)}")

    scenes = segment(epochs, bodies, gap=420.0, per_body=False)
    check(len(scenes) == 1, f"one shared phase should be one scene, got {len(scenes)}")
    check(scenes[0].size == 4, "the scene should contain both shooters' frames")


def test_no_timestamps_yields_no_bursts():
    segs = segment([None, None], ["A", "A"], gap=2.0)
    check(segs == [], "frames without timestamps cannot be segmented")


# --- filename normalization -----------------------------------------------

def test_normalize_stem():
    cases = {
        "IMG_1234.CR2": "img_1234",
        "IMG_1234.jpg": "img_1234",
        "IMG_1234-Edit.jpg": "img_1234",
        "IMG_1234-Edit-2.jpg": "img_1234",
        "IMG_1234-Enhanced-NR.dng": "img_1234",
        "IMG_1234_1.jpg": "img_1234",
        "IMG_1234-copy.jpg": "img_1234",
    }
    for given, want in cases.items():
        got = normalize_stem(given)
        check(got == want, f"normalize_stem({given!r}) = {got!r}, want {want!r}")


# --- end-to-end scan ------------------------------------------------------

def test_scan_end_to_end(tmp: Path):
    info = make_wedding(tmp / "wedding", seed=11)
    records, meta = scan_wedding(
        raw_roots=[info["raw"]],
        delivered_roots=[info["delivered"]],
        prefer_exiftool=False,
    )

    check(len(records) == info["frames"],
          f"scanned {len(records)} records, fixture made {info['frames']}")

    matched = sum(1 for r in records if r.delivered)
    check(matched == info["kept"],
          f"delivered join found {matched}, fixture delivered {info['kept']}")

    by_stem = meta["join"]["matched_by_stem"]
    by_time = meta["join"]["matched_by_time"]
    check(by_time > 0, "renamed exports must be recovered by the capture-time join")
    check(by_stem > 0, "same-name exports must be recovered by the stem join")
    check(meta["join"]["unmatched"] == 0,
          f"{meta['join']['unmatched']} delivered files failed to join")

    timed = sum(1 for r in records if r.exif.capture_epoch is not None)
    check(timed == len(records), f"capture time missing on {len(records) - timed} frames")

    check(meta["bursts"]["count"] > 0, "bursts should be detected")
    check(meta["bursts"]["preference_pairs"] > 0, "decisive bursts should yield pairs")
    check(meta["scenes"]["count"] >= 5, f"expected >=5 scenes, got {meta['scenes']['count']}")

    bodies = {r.exif.body_key for r in records}
    check(len(bodies) == 2, f"expected 2 camera bodies, got {bodies}")

    return records, meta


def test_analysis_and_report(tmp: Path):
    info = make_wedding(tmp / "wedding2", seed=23)
    records, meta = scan_wedding(
        raw_roots=[info["raw"]], delivered_roots=[info["delivered"]], prefer_exiftool=False
    )
    a = analyze(records, meta)

    check(0.0 < a["counts"]["keep_rate"] < 1.0, "keep rate should be a proper fraction")
    check(a["counts"]["capture_time_coverage"] == 1.0, "fixture should have full time coverage")
    check(0.5 < a["counts"]["sidecar_coverage"] < 1.0,
          f"fixture sidecar coverage looks wrong: {a['counts']['sidecar_coverage']}")

    r_auc = a["labels"]["rating_auc"]
    check(r_auc is not None and r_auc > 0.75,
          f"fixture ratings correlate with delivery; AUC={r_auc}")

    check(a["develop"]["with_nontrivial_develop"] > 0, "fixture has real develop settings")
    check(a["develop"]["distinct_fingerprints"] > 1, "fixture develop settings should vary")
    check(a["duplicate_work"]["decisive_bursts"] > 0, "fixture should have decisive bursts")

    # Timeline built from capture times.
    tl = a["timeline"]
    check(tl["span_hours"] > 5, f"fixture spans a full day; got {tl['span_hours']}h")
    check(tl["bodies"] == 2, f"expected 2 bodies, got {tl['bodies']}")
    check(tl["frames_per_hour"] > 0, "shooting rate should be computed")

    scenes = tl["scenes"]
    check(len(scenes) >= 5, f"expected >=5 scenes, got {len(scenes)}")
    check([s["scene"] for s in scenes] == list(range(1, len(scenes) + 1)),
          "scenes should be numbered in chronological order")
    check(sum(s["frames"] for s in scenes) == len(records),
          "every timestamped frame should land in exactly one scene")
    check(sum(s["delivered"] for s in scenes) == sum(r.delivered for r in records),
          "scene delivered counts should sum to the total")
    check(all(":" in s["start_clock"] for s in scenes), "scenes need a clock start time")
    check(any(s["flash_share"] > 0.5 for s in scenes),
          "the fixture's flash-lit scenes should be visible in the table")

    hours = tl["hours"]
    check(len(hours) > 1, "frames should span multiple hours")
    check(sum(h["frames"] for h in hours) == len(records),
          "hour buckets should account for every frame")
    check(hours == sorted(hours, key=lambda h: h["hour"]), "hours should be ordered")

    findings = verdicts(a)
    check(len(findings) >= 6, f"expected a full finding set, got {len(findings)}")
    titles = " ".join(f["title"] for f in findings)
    check("keep/drop label" in titles or "keep rate" in titles,
          "findings should speak to label quality")

    out = tmp / "report-out"
    payload = write_reports(records, meta, out, title="Fixture Wedding")
    check((out / "AUDIT_REPORT.md").exists(), "AUDIT_REPORT.md should be written")
    check((out / "inventory.csv").exists(), "inventory.csv should be written")
    check((out / "audit.json").exists(), "audit.json should be written")

    md = (out / "AUDIT_REPORT.md").read_text(encoding="utf-8")
    check("## Verdict" in md, "report needs a verdict section")
    check("Preference pairs" in md, "report needs burst statistics")
    check("Star rating vs delivery" in md, "report needs the rating/delivery table")
    check("### Scenes" in md, "report needs the per-scene timeline table")
    check("### By hour of day" in md, "report needs the hour-of-day breakdown")
    check("frames/hour" in md, "report needs the shooting rate")

    csv_text = (out / "inventory.csv").read_text(encoding="utf-8")
    header = csv_text.splitlines()[0]
    for col in ("capture_time", "delivered", "burst_id", "rating", "xmp_writer", "match_method"):
        check(col in header, f"inventory.csv missing column {col}")
    check(len(csv_text.splitlines()) == len(records) + 1, "one CSV row per frame plus header")
    return payload


def test_no_delivered_folder_is_a_hard_fail(tmp: Path):
    info = make_wedding(tmp / "wedding3", seed=31)
    records, meta = scan_wedding(
        raw_roots=[info["raw"]], delivered_roots=[], prefer_exiftool=False
    )
    a = analyze(records, meta)
    findings = verdicts(a)
    fails = [f for f in findings if f["status"] == "FAIL"]
    check(any("No delivered set" in f["title"] for f in fails),
          "a missing delivered set must be reported as a FAIL, not silently pass")


# --- snapshots ------------------------------------------------------------

def test_snapshot_diff(tmp: Path):
    work = tmp / "snapwork"
    work.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (work / f"IMG_{i}.xmp").write_text(
            build_xmp(rating=2, label=None, creator="Aftershoot 2.x"), encoding="utf-8"
        )

    snaps = tmp / "snaps"
    before = take_snapshot([work], snaps, tag="post-ai")
    check(before["count"] == 5, f"snapshot should capture 5 sidecars, got {before['count']}")

    # Photographer overrides two of the machine's calls.
    (work / "IMG_0.xmp").write_text(
        build_xmp(rating=5, label="Green", creator="Adobe Lightroom Classic"), encoding="utf-8"
    )
    (work / "IMG_1.xmp").write_text(
        build_xmp(rating=0, label=None, creator="Adobe Lightroom Classic"), encoding="utf-8"
    )
    after = take_snapshot([work], snaps, tag="post-review")

    result = diff_snapshots(Path(before["dir"]), Path(after["dir"]),
                            out_path=tmp / "disagreement.jsonl")
    check(result["changed"] == 2, f"expected 2 changes, got {result['changed']}")
    check(result["unchanged"] == 3, f"expected 3 unchanged, got {result['unchanged']}")
    check(result["promoted"] == 1, f"expected 1 promotion, got {result['promoted']}")
    check(result["demoted"] == 1, f"expected 1 demotion, got {result['demoted']}")
    check((tmp / "disagreement.jsonl").exists(), "disagreement set should be written")

    lines = (tmp / "disagreement.jsonl").read_text(encoding="utf-8").strip().splitlines()
    check(len(lines) == 2, f"jsonl should hold one row per change, got {len(lines)}")


# --- automator ------------------------------------------------------------

def test_automator(tmp: Path):
    from mck.automate import build_rollup, discover_weddings, run_archive

    archive = make_archive(tmp / "archive", n_weddings=3, seed=41)
    found = discover_weddings(archive)
    check(len(found) == 3, f"discovery should find 3 weddings, got {len(found)}")
    check(all(w["raw"] and w["delivered"] for w in found),
          "each discovered wedding needs both a raw and a delivered folder")

    out = tmp / "auto-out"
    logs: list[str] = []
    result = run_archive(archive, out, prefer_exiftool=False, log=logs.append)
    check(result["processed"] == 3, f"expected 3 processed, got {result['processed']}")
    check(result["failed"] == 0, f"{result['failed']} wedding(s) errored")

    check((out / "ROLLUP.md").exists(), "roll-up report should be written")
    check((out / "ledger.json").exists(), "ledger should be written")

    rollup = result["rollup"]
    check(rollup["weddings_audited"] == 3, "roll-up should count 3 weddings")
    check(rollup["total_source_images"] > 0, "roll-up should aggregate frame counts")
    check(rollup["total_preference_pairs"] > 0, "roll-up should aggregate preference pairs")
    check(0.0 < rollup["overall_keep_rate"] < 1.0, "roll-up keep rate should be a fraction")

    # Second run must skip everything — this is what makes it safe to re-run.
    result2 = run_archive(archive, out, prefer_exiftool=False, log=logs.append)
    check(result2["processed"] == 0, f"re-run should process 0, got {result2['processed']}")
    check(result2["skipped"] == 3, f"re-run should skip 3, got {result2['skipped']}")

    md = (out / "ROLLUP.md").read_text(encoding="utf-8")
    check("Dataset scale" in md, "roll-up needs the dataset scale section")
    check("Consistency over time" in md, "roll-up needs the consistency section")


def test_automator_survives_a_broken_wedding(tmp: Path):
    from mck.automate import run_archive

    archive = tmp / "archive2"
    make_wedding(archive / "good-wedding", seed=51)
    # A wedding whose raw folder holds nothing readable.
    broken = archive / "broken-wedding"
    (broken / "raw").mkdir(parents=True)
    (broken / "delivered").mkdir(parents=True)
    (broken / "raw" / "note.txt").write_text("no images here", encoding="utf-8")

    out = tmp / "auto-out2"
    result = run_archive(archive, out, prefer_exiftool=False, log=lambda *_: None)
    check(result["processed"] + result["failed"] == 2,
          "both weddings should be attempted")
    check(result["processed"] >= 1, "the good wedding must still be audited")


# --- CLI ------------------------------------------------------------------

def test_cli(tmp: Path):
    from mck.cli import main

    info = make_wedding(tmp / "cli-wedding", seed=61)
    out = tmp / "cli-out"
    code = main([
        "scan",
        "--raw", str(info["raw"]),
        "--delivered", str(info["delivered"]),
        "--out", str(out),
        "--title", "CLI Test",
        "--no-exiftool",
    ])
    check(code == 0, f"scan should exit 0, got {code}")
    check((out / "AUDIT_REPORT.md").exists(), "CLI scan should write a report")

    code = main(["scan", "--raw", str(tmp / "does-not-exist"), "--out", str(out)])
    check(code == 2, f"missing --raw path should exit 2, got {code}")


def test_catalog_inspection(tmp: Path):
    """A catalog-shaped SQLite file should be introspected, not assumed."""
    import sqlite3
    from mck.catalog import inspect_catalog

    lrcat = tmp / "Test.lrcat"
    conn = sqlite3.connect(lrcat)
    conn.execute(
        "CREATE TABLE Adobe_images (id_local INTEGER, rating REAL, "
        "colorLabels TEXT, pick REAL, captureTime TEXT, fileFormat TEXT)"
    )
    conn.executemany(
        "INSERT INTO Adobe_images VALUES (?,?,?,?,?,?)",
        [(i, 4.0 if i % 3 == 0 else 1.0, "Green" if i % 3 == 0 else "",
          1.0 if i % 4 == 0 else 0.0, "2025-06-14T15:00:00", "RAW") for i in range(30)],
    )
    conn.execute("CREATE TABLE AgLibraryFile (id_local INTEGER, baseName TEXT, extension TEXT)")
    conn.commit()
    conn.close()

    result = inspect_catalog(lrcat)
    check(result["readable"], f"catalog should be readable: {result.get('error')}")
    check(result["tables"]["Adobe_images"]["present"], "Adobe_images should be detected")
    check(result["tables"]["Adobe_images"]["rows"] == 30, "row count should be reported")
    check("pick_distribution" in result, "pick flags should be summarized")
    check(any("pick/reject flag" in f for f in result["findings"]),
          "pick flags should produce a finding about catalog-only state")
    check(not result["tables"]["Adobe_imageDevelopSettings"]["present"],
          "absent tables should be reported absent, not crash")

    missing = inspect_catalog(tmp / "nope.lrcat")
    check(not missing["readable"], "a missing catalog should fail cleanly")


# --- XMP writing ----------------------------------------------------------

def test_xmp_write_preserves_everything_else(tmp: Path):
    """The critical safety property: only the rating changes."""
    from mck.xmpwrite import write_rating

    raw = tmp / "IMG_0001.CR2"
    raw.write_bytes(b"stub")
    sidecar = tmp / "IMG_0001.xmp"
    sidecar.write_text(build_xmp(
        rating=2, label="Blue", creator="Adobe Lightroom Classic",
        develop={"Exposure2012": 0.42, "Highlights2012": -35.0}, masks=True,
    ), encoding="utf-8")
    before = sidecar.read_text(encoding="utf-8")

    # Dry run must not touch the file.
    res = write_rating(raw, rating=5, dry_run=True)
    check(res.action == "updated", f"dry run should report intent, got {res.action}")
    check(sidecar.read_text(encoding="utf-8") == before, "dry run must not write")

    # No backup dir means refuse.
    res = write_rating(raw, rating=5, dry_run=False, backup_dir=None)
    check(res.action == "skipped", "must refuse to modify without a backup dir")
    check(sidecar.read_text(encoding="utf-8") == before, "refusal must not write")

    backups = tmp / "backups"
    res = write_rating(raw, rating=5, label="Green", dry_run=False, backup_dir=backups)
    check(res.action == "updated", f"expected update, got {res.action}: {res.detail}")
    check((backups / "IMG_0001.xmp").exists(), "original must be backed up")
    check((backups / "IMG_0001.xmp").read_text(encoding="utf-8") == before,
          "backup must be byte-identical to the original")

    after = sidecar.read_text(encoding="utf-8")
    doc = parse_xmp(sidecar)
    check(doc.rating == 5, f"rating not updated: {doc.rating}")
    check(doc.label == "Green", f"label not updated: {doc.label}")
    check(abs((doc.get_float("crs:Exposure2012") or 0) - 0.42) < 1e-6,
          "develop settings must survive a rating write")
    check(abs((doc.get_float("crs:Highlights2012") or 0) + 35.0) < 1e-6,
          "all develop settings must survive")
    check(doc.mask_count == 1, "local adjustment masks must survive")
    check("MaskGroupBasedCorrections" in after, "mask block must remain in the file")
    check(len(after) - len(before) < 40,
          "a rating write should change a handful of bytes, not rewrite the file")


def test_xmp_write_element_form_and_creation(tmp: Path):
    from mck.xmpwrite import write_rating

    # Element form rather than attribute form.
    raw = tmp / "IMG_0002.CR2"
    raw.write_bytes(b"stub")
    (tmp / "IMG_0002.xmp").write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        "<xmp:Rating>1</xmp:Rating></rdf:Description></rdf:RDF></x:xmpmeta>",
        encoding="utf-8")
    res = write_rating(raw, rating=4, dry_run=False, backup_dir=tmp / "b")
    check(res.action == "updated", f"element form should update, got {res.action}")
    check(parse_xmp(tmp / "IMG_0002.xmp").rating == 4, "element-form rating not set")

    # No sidecar at all -> create one.
    raw2 = tmp / "IMG_0003.CR2"
    raw2.write_bytes(b"stub")
    res = write_rating(raw2, rating=3, label="Green", dry_run=False, backup_dir=tmp / "b")
    check(res.action == "created", f"expected creation, got {res.action}")
    doc = parse_xmp(tmp / "IMG_0003.xmp")
    check(doc.rating == 3 and doc.label == "Green", "created sidecar has wrong values")

    # Invalid inputs are rejected, not written.
    check(write_rating(raw2, rating=9, dry_run=False).action == "error",
          "out-of-range rating must be rejected")
    check(write_rating(raw2, label="Chartreuse", dry_run=False).action == "error",
          "unknown colour label must be rejected")


# --- culling automator ----------------------------------------------------

def test_cull_automator(tmp: Path):
    from mck.cullwatch import RATING_KEEP, process_wedding, run_intake

    info = make_wedding(tmp / "intake" / "2025-07-04-jones", seed=71)
    # The fixture puts frames in raw/; treat the wedding folder as the drop.
    out = tmp / "cull-out"

    result = process_wedding(
        raw_root=info["raw"], out_dir=out / "jones",
        prefer_exiftool=False, snapshot=True, write_xmp=False, log=lambda *_: None,
    )
    check(result["status"] == "ok", f"cull should succeed: {result}")
    check(result["frames"] == info["frames"], "should see every frame")
    check(result["proposed_keepers"] > 0, "should propose some keepers")
    check(0.05 < result["proposed_keep_rate"] < 0.5,
          f"keep rate should be plausible, got {result['proposed_keep_rate']}")
    check(result["snapshot"] is not None, "must snapshot before proposing")
    check(result["xmp"]["dry_run"] is True, "must default to not writing sidecars")
    check((out / "jones" / "proposal.json").exists(), "proposal must be written")

    # Sidecars must be untouched without --write.
    import json as _json
    proposal = _json.loads((out / "jones" / "proposal.json").read_text())
    check(len(proposal) == info["frames"], "proposal covers every frame")
    check(proposal[0]["proposed_rating"] >= RATING_KEEP,
          "proposal should be sorted best-first")
    check(all("capture_time" in row for row in proposal),
          "proposal rows need capture time")

    # Intake run: skips unsettled folders, then records state.
    intake_out = tmp / "intake-out"
    res = run_intake(tmp / "intake", intake_out, settle_seconds=0.0,
                     prefer_exiftool=False, log=lambda *_: None)
    check(res["processed"] == 1, f"expected 1 processed, got {res['processed']}")
    res2 = run_intake(tmp / "intake", intake_out, settle_seconds=0.0,
                      prefer_exiftool=False, log=lambda *_: None)
    check(res2["processed"] == 0 and res2["skipped"] == 1,
          "second run must not reprocess")


def test_cull_waits_for_copy_to_finish(tmp: Path):
    from mck.cullwatch import run_intake

    make_wedding(tmp / "intake2" / "still-copying", seed=73)
    res = run_intake(tmp / "intake2", tmp / "out2", settle_seconds=9999.0,
                     prefer_exiftool=False, log=lambda *_: None)
    check(res["processed"] == 0, "must not process a folder that is still changing")
    check(res["waiting"] == 1, f"should report it as waiting, got {res}")


def test_cull_write_mode_backs_up(tmp: Path):
    from mck.cullwatch import process_wedding

    info = make_wedding(tmp / "w", seed=77)
    sidecars = sorted(info["raw"].glob("*.xmp"))
    check(len(sidecars) > 0, "fixture should have sidecars")
    original = sidecars[0].read_text(encoding="utf-8")

    result = process_wedding(
        raw_root=info["raw"], out_dir=tmp / "out", prefer_exiftool=False,
        write_xmp=True, snapshot=False, log=lambda *_: None,
    )
    check(result["xmp"]["dry_run"] is False, "write mode should be active")
    backup_dir = Path(result["xmp"]["backup_dir"])
    check(backup_dir.exists(), "backup directory must be created")
    check((backup_dir / sidecars[0].name).read_text(encoding="utf-8") == original,
          "backup must preserve the original sidecar exactly")
    check(not result["xmp"]["errors"], f"no write errors expected: {result['xmp']['errors']}")


# --- editorial automator --------------------------------------------------

def _editorial_gallery(tmp: Path, count: int = 90, width: int = 2000,
                       height: int = 1400) -> Path:
    """A delivered gallery with real JPEG dimensions and spread capture times."""
    from make_fixtures import build_jpeg_with_exif, build_tiff_exif
    from datetime import timedelta as _td

    gallery = tmp / "delivered"
    gallery.mkdir(parents=True, exist_ok=True)
    base = datetime(2025, 6, 14, 11, 0, 0)
    for i in range(count):
        # Five phases, 40 minutes apart, so scene segmentation finds them.
        phase, within = divmod(i, count // 5 or 1)
        shot = base + _td(minutes=phase * 40 + within)
        tiff = build_tiff_exif(shot, "Canon", "EOS R5", "BODY-A", "RF50mm",
                               400, 2.0, 1 / 500, 50.0, False)
        jpeg = build_jpeg_with_exif(tiff)
        # Append padding after EOI so SOF parsing still works but size varies.
        (gallery / f"McKinley-{i:04d}.jpg").write_bytes(
            _sof_jpeg(width, height, jpeg)
        )
    return gallery


def _sof_jpeg(width: int, height: int, prefix: bytes) -> bytes:
    """Splice a real SOF0 marker into a fixture JPEG so dimensions are readable."""
    import struct as _s
    sof = b"\xff\xc0" + _s.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    # prefix ends with EOI; insert SOF before it.
    return prefix[:-2] + sof + prefix[-2:]


def test_jpeg_dimensions(tmp: Path):
    from mck.exif import jpeg_dimensions
    from make_fixtures import build_jpeg_with_exif, build_tiff_exif

    tiff = build_tiff_exif(datetime(2025, 1, 1, 12, 0, 0), "Canon", "R5", "A", "L",
                           100, 2.0, 1 / 100, 50.0, False)
    p = tmp / "x.jpg"
    p.write_bytes(_sof_jpeg(1600, 1067, build_jpeg_with_exif(tiff)))
    check(jpeg_dimensions(p) == (1600, 1067), f"got {jpeg_dimensions(p)}")

    bad = tmp / "bad.jpg"
    bad.write_bytes(b"not a jpeg")
    check(jpeg_dimensions(bad) is None, "non-JPEG should return None, not raise")


def test_editorial_ready(tmp: Path):
    from mck.editorial import (
        PROFILES, SubmissionLedger, VENDOR_ROLES, DETAIL_CATEGORIES,
        evaluate, meta_template, render_submission,
    )

    gallery = _editorial_gallery(tmp, count=90)
    ledger = SubmissionLedger(tmp / "submissions.json")
    meta = meta_template("2025-06-14-smith")
    meta["vendors"] = {r: f"{r} co" for r in VENDOR_ROLES}
    meta["details_present"] = {c: True for c in DETAIL_CATEGORIES}

    report = evaluate("2025-06-14-smith", [gallery], PROFILES["style-me-pretty"],
                      ledger, meta, prefer_exiftool=False)

    check(report["counts"]["delivered"] == 90, f"got {report['counts']['delivered']}")
    check(report["counts"]["spec_failures"] == 0,
          f"2000x1400 should pass SMP spec: {report['spec_failures'][:2]}")
    check(report["ready"], f"should be ready: {report['blockers']}")
    check(60 <= report["counts"]["selected"] <= 150, "selection within SMP range")
    check(report["coverage"]["scenes_covered"] >= 5,
          f"selection should span the day, got {report['coverage']['scenes_covered']}")
    check(not report["warnings"], f"complete metadata should warn nothing: {report['warnings']}")

    md = render_submission(report)
    check("Ready to submit" in md, "report should say it is ready")
    check("Vendor credits" in md and "Coverage" in md, "report needs its sections")


def test_editorial_blocks_on_spec_and_count(tmp: Path):
    from mck.editorial import PROFILES, SubmissionLedger, evaluate, meta_template

    # Too small for the 900px short-edge requirement.
    gallery = _editorial_gallery(tmp, count=70, width=800, height=600)
    ledger = SubmissionLedger(tmp / "s.json")
    report = evaluate("small", [gallery], PROFILES["style-me-pretty"], ledger,
                      meta_template("small"), prefer_exiftool=False)
    check(report["counts"]["spec_failures"] == 70, "all frames should fail the spec")
    check(not report["ready"], "must not be ready")
    check(any("at least 60" in b for b in report["blockers"]),
          f"should block on count: {report['blockers']}")
    check(any("Photography credit" in b for b in report["blockers"]),
          "missing photographer credit must block")


def test_editorial_exclusivity(tmp: Path):
    from mck.editorial import (
        PROFILES, SubmissionLedger, VENDOR_ROLES, DETAIL_CATEGORIES,
        evaluate, meta_template,
    )

    gallery = _editorial_gallery(tmp, count=90)
    ledger = SubmissionLedger(tmp / "submissions.json")
    meta = meta_template("smith")
    meta["vendors"] = {r: "x" for r in VENDOR_ROLES}
    meta["details_present"] = {c: True for c in DETAIL_CATEGORIES}

    # Pending elsewhere, inside the response window -> blocked.
    ledger.record("smith", "junebug", status="pending",
                  when=datetime.now().date().isoformat(), response_days=28)
    report = evaluate("smith", [gallery], PROFILES["style-me-pretty"], ledger, meta,
                      prefer_exiftool=False)
    check(not report["ready"], "pending elsewhere must block")
    check(any("exclusivity" in b.lower() for b in report["blockers"]),
          f"should cite exclusivity: {report['blockers']}")

    # Same submission, now overdue -> warning instead of a block.
    old = (datetime.now() - timedelta(days=60)).date().isoformat()
    ledger.data["weddings"]["smith"][0]["submitted"] = old
    report = evaluate("smith", [gallery], PROFILES["style-me-pretty"], ledger, meta,
                      prefer_exiftool=False)
    check(report["ready"], f"an overdue window should unblock: {report['blockers']}")
    check(any("past their" in w for w in report["warnings"]),
          f"should warn about the lapsed window: {report['warnings']}")

    # Already published elsewhere -> hard block, permanently.
    ledger.set_status("smith", "junebug", "published")
    report = evaluate("smith", [gallery], PROFILES["style-me-pretty"], ledger, meta,
                      prefer_exiftool=False)
    check(not report["ready"], "prior publication must block")
    check(any("published" in b.lower() for b in report["blockers"]),
          f"should cite prior publication: {report['blockers']}")


def test_editorial_cli(tmp: Path):
    from mck.cli import main

    out = tmp / "editorial"
    check(main(["editorial", "profiles", "--out", str(out)]) == 0,
          "profiles should list cleanly")
    check(main(["editorial", "init", "--wedding", "smith", "--out", str(out)]) == 0,
          "init should create metadata")
    check((out / "weddings" / "smith.json").exists(), "metadata file should exist")
    check(main(["editorial", "init", "--wedding", "smith", "--out", str(out)]) == 2,
          "init must not silently overwrite")

    gallery = _editorial_gallery(tmp, count=90)
    code = main(["editorial", "check", "--wedding", "smith",
                 "--delivered", str(gallery), "--publication", "style-me-pretty",
                 "--out", str(out), "--no-exiftool"])
    check(code == 1, "incomplete vendor credits should exit non-zero")
    check((out / "weddings" / "smith" / "SUBMISSION-style-me-pretty.md").exists(),
          "submission package should still be written")

    check(main(["editorial", "submit", "--wedding", "smith",
                "--publication", "junebug", "--out", str(out)]) == 0,
          "submit should record")
    check(main(["editorial", "status", "--wedding", "smith", "--publication", "junebug",
                "--set", "declined", "--out", str(out)]) == 0,
          "status should update")
    check(main(["editorial", "status", "--wedding", "smith", "--publication", "nope",
                "--set", "declined", "--out", str(out)]) == 1,
          "unknown submission should fail cleanly")


# --- runner ---------------------------------------------------------------

def run_all():
    tests = [
        test_exif_tiff_roundtrip, test_exif_jpeg, test_exif_missing,
        test_xmp_attributes, test_xmp_element_form,
        test_xmp_zero_develop_is_not_an_edit, test_xmp_malformed,
        test_auc, test_entropy,
        test_burst_segmentation, test_bursts_split_by_body, test_scenes_span_bodies,
        test_no_timestamps_yields_no_bursts,
        test_normalize_stem,
        test_scan_end_to_end, test_analysis_and_report,
        test_no_delivered_folder_is_a_hard_fail,
        test_snapshot_diff,
        test_automator, test_automator_survives_a_broken_wedding,
        test_cli, test_catalog_inspection,
        test_xmp_write_preserves_everything_else, test_xmp_write_element_form_and_creation,
        test_cull_automator, test_cull_waits_for_copy_to_finish, test_cull_write_mode_backs_up,
        test_jpeg_dimensions, test_editorial_ready, test_editorial_blocks_on_spec_and_count,
        test_editorial_exclusivity, test_editorial_cli,
    ]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, fn in enumerate(tests):
            print(f"[{i + 1:2}/{len(tests)}] {fn.__name__}")
            sub = tmp / fn.__name__
            sub.mkdir(parents=True, exist_ok=True)
            try:
                fn(sub) if fn.__code__.co_argcount else fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                FAILURES.append(f"{fn.__name__} raised {exc}")
                traceback.print_exc()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"All {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_all())
