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
    build_jpeg_with_exif, build_raw_with_preview, build_sof_jpeg, build_tiff_exif,
    build_xmp, make_archive, make_wedding,
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


def _build_catalog(tmp: Path, stems: list[str], published: set[str],
                   picked: set[str] | None = None) -> Path:
    """A catalog shaped like Lightroom's, with the tables the label path uses."""
    import sqlite3
    picked = picked or set()
    tmp.mkdir(parents=True, exist_ok=True)
    lrcat = tmp / "Wedding.lrcat"
    conn = sqlite3.connect(lrcat)
    conn.executescript("""
        CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rating REAL,
            colorLabels TEXT, pick REAL, captureTime TEXT, fileFormat TEXT,
            touchTime REAL, rootFile INTEGER, masterImage INTEGER);
        CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, baseName TEXT,
            extension TEXT, folder INTEGER, originalFilename TEXT);
        CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY,
            pathFromRoot TEXT, rootFolder INTEGER);
        CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY,
            absolutePath TEXT, name TEXT);
        CREATE TABLE AgLibraryCollection (id_local INTEGER PRIMARY KEY, name TEXT,
            parent INTEGER, systemOnly INTEGER);
        CREATE TABLE AgLibraryCollectionImage (id_local INTEGER PRIMARY KEY,
            collection INTEGER, image INTEGER);
        CREATE TABLE AgLibraryPublishedCollection (id_local INTEGER PRIMARY KEY,
            name TEXT, remoteCollectionId TEXT);
        CREATE TABLE AgRemotePhoto (id_local INTEGER PRIMARY KEY, collection INTEGER,
            photo INTEGER, mostRecentPublishTime REAL, remoteId TEXT);
        CREATE TABLE Adobe_libraryImageDevelopHistoryStep (id_local INTEGER PRIMARY KEY,
            image INTEGER, dateCreated REAL, name TEXT, relValueString TEXT);
    """)
    conn.execute("INSERT INTO AgLibraryRootFolder VALUES (1,'/Volumes/The Beast/','Beast')")
    conn.execute("INSERT INTO AgLibraryFolder VALUES (1,'2025-06-14 Smith/RAW/',1)")
    conn.execute("INSERT INTO AgLibraryPublishedCollection VALUES (1,'Pic-Time Gallery','g1')")
    conn.execute("INSERT INTO AgLibraryCollection VALUES (1,'Smith Final Delivery',NULL,0)")

    for i, stem in enumerate(stems, start=1):
        conn.execute("INSERT INTO AgLibraryFile VALUES (?,?,?,?,?)",
                     (i, stem, "CR2", 1, stem + ".CR2"))
        conn.execute(
            "INSERT INTO Adobe_images (id_local,rating,colorLabels,pick,captureTime,"
            "fileFormat,rootFile) VALUES (?,?,?,?,?,?,?)",
            (i, 4.0 if stem in published else 1.0,
             "Green" if stem in published else "",
             1.0 if stem in picked else 0.0,
             "2025-06-14T15:00:00", "RAW", i))
        if stem in published:
            conn.execute("INSERT INTO AgRemotePhoto (collection,photo,remoteId) VALUES (1,?,?)",
                         (i, f"r{i}"))
            conn.execute("INSERT INTO AgLibraryCollectionImage (collection,image) VALUES (1,?)",
                         (i,))
    conn.execute("INSERT INTO Adobe_libraryImageDevelopHistoryStep (image,dateCreated,name) "
                 "VALUES (1,0,'Exposure')")
    conn.commit()
    conn.close()
    return lrcat


def test_catalog_label_discovery(tmp: Path):
    from mck.catalog import (
        delivered_stems, discover_label_sources, extract_images, inspect_catalog,
        open_catalog, read_extract, render_catalog_report, write_extract,
    )

    stems = [f"IMG_{1000 + i}" for i in range(40)]
    published = set(stems[:10])
    picked = set(stems[:14])
    lrcat = _build_catalog(tmp, stems, published, picked)

    with open_catalog(lrcat) as conn:
        sources = discover_label_sources(conn)
        rows = extract_images(conn)

    check(sources["total_images"] == 40, f"got {sources['total_images']}")
    kinds = [c["kind"] for c in sources["candidates"]]
    check(kinds[0] == "published", f"published must rank first, got {kinds}")
    for expected in ("published", "collection", "pick", "color", "rating"):
        check(expected in kinds, f"{expected} should be discovered, got {kinds}")

    pub = next(c for c in sources["candidates"] if c["kind"] == "published")
    check(pub["images"] == 10, f"published count wrong: {pub['images']}")
    check(any("Pic-Time" in (c["name"] or "") for c in pub["collections"]),
          "publish service name should be surfaced")

    coll = next(c for c in sources["candidates"] if c["kind"] == "collection")
    check(any("Final Delivery" in c["name"] for c in coll["collections"]),
          "delivery-named collection should be detected")

    check(len(rows) == 40, f"extract should return every image, got {len(rows)}")
    check(all(r["folder"].startswith("/Volumes/The Beast/") for r in rows),
          "folder path should join root + pathFromRoot")

    check(delivered_stems(rows, "published") == {s.lower() for s in published},
          "published stems wrong")
    check(len(delivered_stems(rows, "pick")) == 14, "pick stems wrong")
    check(len(delivered_stems(rows, "rating", threshold=3)) == 10, "rating stems wrong")
    check(len(delivered_stems(rows, "collection", collection="final delivery")) == 10,
          "collection stems wrong")
    check(delivered_stems(rows, "rating", threshold=5) == set(),
          "an unreachable threshold should select nothing")

    out = tmp / "labels.csv"
    write_extract(rows, out)
    reloaded = read_extract(out)
    check(len(reloaded) == 40, "round-trip should preserve every row")
    check(delivered_stems(reloaded, "published") == delivered_stems(rows, "published"),
          "round-trip must preserve the published flag")

    report = inspect_catalog(lrcat)
    check(report["readable"], f"catalog should be readable: {report.get('error')}")
    check(any("Strongest available label" in f for f in report["findings"]),
          f"should recommend a label source: {report['findings']}")
    text = render_catalog_report(report)
    check("--label-source published" in text, "report should name the flag to use")
    check("2025-06-14 Smith" in text, "folder summary should locate the wedding")

    filtered = inspect_catalog(lrcat, folder_filter="nonexistent-wedding")
    check(filtered["folder_matches"] == 0, "a bad folder filter should match nothing")

    missing = inspect_catalog(tmp / "nope.lrcat")
    check(not missing["readable"], "a missing catalog should fail cleanly")


def test_catalog_missing_tables_degrade(tmp: Path):
    """A catalog without the optional tables must still yield what it has."""
    import sqlite3
    from mck.catalog import discover_label_sources, extract_images, open_catalog

    lrcat = tmp / "Sparse.lrcat"
    conn = sqlite3.connect(lrcat)
    conn.execute("CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rating REAL, "
                 "rootFile INTEGER)")
    conn.executemany("INSERT INTO Adobe_images VALUES (?,?,?)",
                     [(i, 4.0 if i < 5 else 0.0, i) for i in range(20)])
    conn.commit(); conn.close()

    with open_catalog(lrcat) as conn:
        sources = discover_label_sources(conn)
        rows = extract_images(conn)
    kinds = [c["kind"] for c in sources["candidates"]]
    check(kinds == ["rating"], f"only ratings exist here, got {kinds}")
    check(len(rows) == 20, "extraction should still work without the join tables")
    check(all(r["stem"] == "" for r in rows), "missing filenames should be blank, not crash")


def test_scan_with_catalog_labels(tmp: Path):
    """The audit must run with no delivered folder anywhere on disk."""
    from mck.catalog import delivered_stems, extract_images, open_catalog

    info = make_wedding(tmp / "wedding", seed=101)
    raw_stems = sorted(p.stem for p in info["raw"].glob("*.CR2"))
    # Publish exactly the frames the fixture treated as delivered, which is how
    # a real catalog relates to a real gallery. Publishing an arbitrary subset
    # would decorrelate the ratings and the audit would correctly call that out.
    published = {p.stem for p in info["delivered"].glob("*.jpg")
                 if p.stem in set(raw_stems)}
    lrcat = _build_catalog(tmp, raw_stems, published)

    with open_catalog(lrcat) as conn:
        rows = extract_images(conn)
    stems = delivered_stems(rows, "published")

    records, meta = scan_wedding(
        raw_roots=[info["raw"]], delivered_roots=[], prefer_exiftool=False,
        label_stems=stems,
    )
    matched = sum(1 for r in records if r.delivered)
    check(matched == len(published),
          f"catalog label should mark {len(published)} frames, got {matched}")
    check(meta["join"]["matched_by_catalog"] == len(published),
          "join stats should record the catalog match")
    check(all(r.match_method == "catalog" for r in records if r.delivered),
          "delivered frames should be attributed to the catalog")

    a = analyze(records, meta)
    findings = verdicts(a)
    check(not any(f["status"] == "FAIL" for f in findings),
          f"a catalog-labelled wedding should not fail: "
          f"{[f['title'] for f in findings if f['status'] == 'FAIL']}")
    check(any("Catalog label matched" in f["title"] for f in findings),
          f"should report the catalog join: {[f['title'] for f in findings]}")
    check(a["bursts"]["preference_pairs"] > 0, "preference pairs should still be computed")


def test_scan_cli_requires_a_label(tmp: Path):
    from mck.cli import main
    info = make_wedding(tmp / "w", seed=103)
    code = main(["scan", "--raw", str(info["raw"]), "--out", str(tmp / "o"), "--no-exiftool"])
    check(code == 2, f"no --delivered and no --labels must fail clearly, got {code}")


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


def test_discover_survey(tmp: Path):
    """The survey must explain a zero-match archive, not just report zero."""
    from mck.automate import render_survey, survey_archive

    archive = tmp / "TheBeast"
    # One folder using conventional names, one using McKinley-ish naming.
    make_wedding(archive / "2025-06-14 Smith", seed=91)
    odd = archive / "2025-08-02 Jones"
    (odd / "CR3 Files").mkdir(parents=True)
    (odd / "Client Gallery").mkdir(parents=True)
    (odd / "CR3 Files" / "IMG_1.CR2").write_bytes(b"x")
    (odd / "Client Gallery" / "IMG_1.jpg").write_bytes(b"x")

    s = survey_archive(archive)
    check(s["exists"], "archive should be found")
    check(s["matched"] == 1, f"one folder uses known names, got {s['matched']}")
    check(s["unmatched"] == 1, f"one folder should be skipped, got {s['unmatched']}")

    names = {c["name"]: c for c in s["candidates"]}
    check(names["2025-08-02 Jones"]["would_match"] is False, "odd naming should not match")
    check("cr3 files" in s["subfolder_names"], "unmatched names should be surfaced")
    check(names["2025-08-02 Jones"]["raw_files_seen"] == 1, "should count raw files")
    check(names["2025-08-02 Jones"]["jpeg_files_seen"] == 1, "should count jpegs")

    text = render_survey(s)
    check("SKIP" in text and "OK" in text, "survey should mark both outcomes")
    check("--raw-names" in text, "survey should suggest the fix when something is skipped")

    # Feeding the real names back in should make it match.
    s2 = survey_archive(archive, raw_names=["cr3 files"], delivered_names=["client gallery"])
    check(s2["matched"] == 1, f"custom names should match the odd folder, got {s2['matched']}")

    missing = survey_archive(tmp / "nope")
    check(not missing["exists"], "missing archive should report cleanly")
    check("not found" in render_survey(missing), "should say so")


def test_discover_cli(tmp: Path):
    from mck.cli import main

    archive = tmp / "arch"
    make_wedding(archive / "2025-06-14 Smith", seed=93)
    check(main(["discover", "--archive", str(archive)]) == 0, "discover should exit 0 on a match")
    check(main(["discover", "--archive", str(tmp / "nope")]) == 2, "missing archive exits 2")


def test_bootstrap(tmp: Path):
    """One command must produce one handoff file covering drives and catalogs."""
    from mck.bootstrap import find_catalogs, render, run

    archive = tmp / "Beast"
    make_wedding(archive / "2025-06-14 Smith", seed=111)
    odd = archive / "2024-09-21 Alvarez"
    (odd / "CR3 Files").mkdir(parents=True)
    (odd / "CR3 Files" / "IMG_1.CR2").write_bytes(b"x")

    stems = [f"IMG_{2000 + i}" for i in range(20)]
    lrcat = _build_catalog(tmp / "cat", stems, set(stems[:6]))
    # A dated duplicate in a Backups folder must be recognised and skipped.
    backups = tmp / "cat" / "Backups" / "2025-01-01"
    backups.mkdir(parents=True)
    (backups / "Wedding.lrcat").write_bytes(lrcat.read_bytes())

    found = find_catalogs(extra_roots=[tmp / "cat"])
    check(len(found) == 2, f"both catalogs should be found, got {len(found)}")
    check(sum(1 for f in found if f["is_backup"]) == 1,
          "the Backups copy should be flagged as a backup")
    check(found[0]["is_backup"] is False, "real catalogs should sort ahead of backups")

    out = tmp / "handoff"
    payload = run(out_dir=out, archives=[archive], catalogs=None,
                  skip_catalog_search=True, log=lambda *_: None)
    check(len(payload["archives"]) == 1, "the archive should be surveyed")
    check(payload["catalog_files"] == [], "catalog search was skipped")

    payload = run(out_dir=out, archives=[archive], catalogs=[lrcat],
                  log=lambda *_: None)
    check(len(payload["catalogs"]) == 1, "the given catalog should be inspected")
    check(payload["catalogs"][0]["readable"], "the catalog should read")

    check((out / "HANDOFF.md").exists(), "handoff report should be written")
    check((out / "handoff.json").exists(), "handoff json should be written")

    md = (out / "HANDOFF.md").read_text(encoding="utf-8")
    check("## Archives" in md and "## Lightroom catalogs" in md,
          "handoff needs both sections")
    check("2025-06-14 Smith" in md, "archive folders should appear")
    check("--label-source" in md, "handoff should name the label flag to use")
    check("client names" in md, "handoff must carry the privacy note")

    # A drive that is not attached must not break the pass.
    payload = run(out_dir=tmp / "h2", archives=[tmp / "not-attached"],
                  skip_catalog_search=True, log=lambda *_: None)
    check(payload["archives"][0]["exists"] is False, "a missing drive is reported, not fatal")
    check("Archive not found" in render(payload), "handoff should say the drive is missing")


def test_bootstrap_cli(tmp: Path):
    from mck.cli import main

    archive = tmp / "Beast"
    make_wedding(archive / "2025-06-14 Smith", seed=113)
    stems = [f"IMG_{i}" for i in range(10)]
    lrcat = _build_catalog(tmp / "cat", stems, set(stems[:3]))

    code = main(["bootstrap", "--out", str(tmp / "hand"),
                 "--archive", str(archive), "--catalog", str(lrcat)])
    check(code == 0, f"bootstrap should exit 0, got {code}")
    check((tmp / "hand" / "HANDOFF.md").exists(), "CLI should write the handoff")


def test_star_taxonomy(tmp: Path):
    """Stars 1-3 are reason codes; 3 next to a 4 is an annotated preference pair."""
    from mck.report import RATING_MEANING, _taxonomy
    from mck.scan import ImageRecord
    from mck.exif import ExifRecord

    def rec(stem, star, burst, delivered, label=None):
        r = ImageRecord(path=Path(stem), stem=stem, ext=".cr2", size_bytes=1,
                        exif=ExifRecord())
        r.burst_id = burst
        r.delivered = delivered
        r.xmp = type("X", (), {
            "rating": star, "label": label,
            "process_version": None, "has_develop": False,
            "has_nontrivial_develop": False, "already_applied": None,
            "converted_to_grayscale": None, "preset_name": None, "mask_count": 0,
        })()
        return r

    records = [
        # Burst 0: a 4-star winner over two 3-star duplicates -> 2 pairs.
        rec("a", 4, 0, True), rec("b", 3, 0, False), rec("c", 3, 0, False),
        # Burst 1: a 5-star highlight over one duplicate -> 1 pair.
        rec("d", 5, 1, True, "Green"), rec("e", 3, 1, False),
        # Burst 2: duplicates with no winner -> no pairs, hurts consistency.
        rec("f", 3, 2, False),
        # Outright rejects, not part of any preference.
        rec("g", 1, 3, False), rec("h", 2, 3, False), rec("i", 2, 4, False),
        # A delivered frame the star pass rated down: a false negative.
        rec("j", 3, 5, True),
    ]

    t = _taxonomy(records)

    check(t["annotated_preference_pairs"] == 3,
          f"expected 3 annotated pairs, got {t['annotated_preference_pairs']}")
    check(t["duplicates_total"] == 5, f"expected 5 threes, got {t['duplicates_total']}")
    check(t["duplicates_in_a_burst_with_a_winner"] == 3,
          f"expected 3 threes beside a winner, got {t['duplicates_in_a_burst_with_a_winner']}")
    check(abs(t["duplicate_consistency"] - 0.6) < 1e-9,
          f"consistency wrong: {t['duplicate_consistency']}")
    check(t["blur_labels"] == 2, f"expected 2 blur labels, got {t['blur_labels']}")
    check(t["highlights"] == 1, f"expected 1 highlight, got {t['highlights']}")

    check(t["by_star"][3]["meaning"] == RATING_MEANING[3], "meanings should be attached")
    check(t["by_star"][3]["n"] == 5, "star 3 count wrong")
    check(t["by_star"][3]["delivered"] == 1, "one 3-star frame was delivered")

    sr = t["star_rule"]
    check(sr["true_positive"] == 2, f"tp wrong: {sr['true_positive']}")
    check(sr["false_positive"] == 0, f"fp wrong: {sr['false_positive']}")
    check(sr["false_negative"] == 1, f"fn wrong: {sr['false_negative']}")
    check(sr["precision"] == 1.0, f"precision wrong: {sr['precision']}")
    check(abs(sr["recall"] - 2 / 3) < 1e-4, f"recall wrong: {sr['recall']}")

    check(t["colors"]["Green"]["n"] == 1, "colour usage should be counted")
    check(t["colors"]["Green"]["delivered"] == 1, "colour delivery should be counted")

    # An archive with no ratings at all must not blow up.
    empty = _taxonomy([rec("z", None, 0, False)])
    check(empty["annotated_preference_pairs"] == 0, "unrated archive yields no pairs")
    check(empty["duplicate_consistency"] == 0.0, "unrated archive has no consistency")
    check(empty["colors"] == {}, "no colours means an empty table")


# --- preview extraction ---------------------------------------------------

def test_preview_extraction(tmp: Path):
    """Pick the largest embedded JPEG, not the first, and not a thumbnail."""
    from mck.preview import extract_preview, find_jpeg_streams

    tiff = build_tiff_exif(datetime(2025, 6, 14, 12, 0, 0), "Canon", "R5", "A",
                           "RF50mm", 400, 2.0, 1 / 500, 50.0, False)
    raw = tmp / "IMG_0001.CR2"
    raw.write_bytes(build_raw_with_preview(tiff, width=1600, height=1067))

    streams = find_jpeg_streams(raw.read_bytes())
    check(len(streams) >= 1, f"should find embedded streams, got {len(streams)}")
    check(streams[0][2] == 1600 and streams[0][3] == 1067,
          f"largest stream should come first, got {streams[0][2]}x{streams[0][3]}")

    result = extract_preview(raw)
    check(result is not None, "preview should be extracted")
    data, w, h = result
    check((w, h) == (1600, 1067), f"wrong preview size: {w}x{h}")
    check(data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"),
          "extracted bytes should be a complete JPEG")

    # The 160px thumbnail is below the floor and must never be returned.
    check(all(min(s[2], s[3]) >= 400 for s in streams),
          "thumbnails should be filtered out")

    # max_edge should prefer the smallest stream that still clears the bar.
    raw2 = tmp / "IMG_0002.CR2"
    raw2.write_bytes(tiff + build_sof_jpeg(800, 600) + build_sof_jpeg(4000, 3000))
    small = extract_preview(raw2, max_edge=700)
    check(small is not None and small[1] == 800,
          f"max_edge should pick the 800px stream, got {small and small[1]}")
    big = extract_preview(raw2, max_edge=3000)
    check(big is not None and big[1] == 4000,
          f"a high max_edge should pick the 4000px stream, got {big and big[1]}")

    # A file with no embedded JPEG must return None rather than raise.
    bare = tmp / "bare.CR2"
    bare.write_bytes(b"\x00" * 4096)
    check(extract_preview(bare) is None, "no preview should yield None")
    check(extract_preview(tmp / "missing.CR2") is None, "missing file should yield None")


# --- burst labelling ------------------------------------------------------

def test_label_task_building(tmp: Path):
    from mck.labeler import build_tasks
    from mck.exif import ExifRecord
    from mck.scan import ImageRecord

    def rec(stem, star, burst, epoch):
        r = ImageRecord(path=Path(stem), stem=stem, ext=".cr2", size_bytes=1,
                        exif=ExifRecord(capture_epoch=epoch))
        r.burst_id = burst
        r.xmp = type("X", (), {"rating": star, "label": None})()
        return r

    records = [
        rec("a", 4, 0, 10.0), rec("b", 3, 0, 10.3), rec("c", 3, 0, 10.6),
        rec("d", 3, 1, 50.0), rec("e", 3, 1, 50.3),          # no winner
        rec("f", 5, 2, 90.0), rec("g", 3, 2, 90.3),
        rec("h", 1, 3, 120.0),                               # single frame
    ]

    decisive = build_tasks(records, mode="decisive")
    check([t.burst_id for t in decisive] == [0, 2],
          f"only bursts with a winner and a loser, got {[t.burst_id for t in decisive]}")
    check(decisive[0].original_winner == "a", "winner should be the highest rated")
    check([f.stem for f in decisive[0].frames] == ["a", "b", "c"],
          "frames should be in capture order")
    check(len({f.index for t in decisive for f in t.frames}) == 5,
          "frame indices must be unique across tasks")

    every = build_tasks(records, mode="all")
    check([t.burst_id for t in every] == [0, 1, 2],
          f"'all' should keep every multi-frame burst, got {[t.burst_id for t in every]}")
    check(every[1].original_winner is None,
          "a burst with no 4-or-5 has no recorded winner")

    check(decisive[0].as_json().get("original_winner") is None,
          "the original pick must NOT be sent to the browser before a choice")


def test_decision_log(tmp: Path):
    from mck.labeler import DecisionLog

    path = tmp / "decisions.jsonl"
    log = DecisionLog(path)
    check(log.stats()["decided"] == 0, "a fresh log is empty")
    check(log.stats()["self_consistency"] is None, "no data means no consistency figure")

    log.record({"burst_id": 1, "frames": ["a", "b", "c"], "chosen": "a",
                "original": "a", "agreed": True, "skipped": False})
    log.record({"burst_id": 2, "frames": ["d", "e"], "chosen": "e",
                "original": "d", "agreed": False, "skipped": False})
    log.record({"burst_id": 3, "frames": ["f", "g"], "chosen": None,
                "original": "f", "agreed": False, "skipped": True})

    st = log.stats()
    check(st["decided"] == 2, f"two real decisions, got {st['decided']}")
    check(st["skipped"] == 1, f"one skip, got {st['skipped']}")
    check(st["agreed"] == 1, f"one agreement, got {st['agreed']}")
    check(abs(st["self_consistency"] - 0.5) < 1e-9,
          f"consistency should be 0.5, got {st['self_consistency']}")
    check(st["preference_pairs"] == 3, f"2 + 1 pairs, got {st['preference_pairs']}")

    # Reopening must resume, not restart.
    again = DecisionLog(path)
    check(len(again.done) == 3, f"log should reload 3 rows, got {len(again.done)}")
    check(again.stats() == st, "reloaded stats should match")


def test_label_server_round_trip(tmp: Path):
    """Start the real server and drive it the way the browser does."""
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from mck.labeler import DecisionLog, _Handler, build_tasks, render_summary

    info = make_wedding(tmp / "wedding", seed=131)
    from mck.scan import scan_wedding
    records, _ = scan_wedding(raw_roots=[info["raw"]], delivered_roots=[],
                              prefer_exiftool=False)
    tasks = build_tasks(records, mode="all")
    check(len(tasks) > 0, "the fixture should yield bursts")

    log = DecisionLog(tmp / "d.jsonl")
    _Handler.tasks = tasks
    _Handler.frames_by_index = {f.index: f for t in tasks for f in t.frames}
    _Handler.log = log
    _Handler.max_edge = 1400

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        check("Which one would you deliver?" in page, "the page should render")

        d = _json.loads(urllib.request.urlopen(base + "/api/tasks").read())
        check(len(d["tasks"]) == len(tasks), "all tasks pending initially")
        check("original_winner" not in d["tasks"][0],
              "the original pick must not leak to the browser")

        idx = d["tasks"][0]["frames"][0]["index"]
        img = urllib.request.urlopen(f"{base}/api/preview?i={idx}").read()
        check(img.startswith(b"\xff\xd8"), "preview endpoint should return a JPEG")

        bad = urllib.request.urlopen(f"{base}/api/preview?i=999999", timeout=5)
        check(False, "an unknown frame index should 404")
    except urllib.error.HTTPError as exc:
        check(exc.code == 404, f"unknown index should 404, got {exc.code}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"server round trip failed: {exc}")

    try:
        task = tasks[0]
        body = _json.dumps({"burst_id": task.burst_id,
                            "chosen": task.frames[-1].stem}).encode()
        req = urllib.request.Request(base + "/api/choose", data=body,
                                     headers={"Content-Type": "application/json"})
        res = _json.loads(urllib.request.urlopen(req).read())
        check(res["stats"]["decided"] == 1, "the choice should be recorded")
        check("original" in res, "the original pick is revealed only after choosing")

        # A frame that is not in this burst must be rejected.
        bad_body = _json.dumps({"burst_id": task.burst_id, "chosen": "nope"}).encode()
        bad_req = urllib.request.Request(base + "/api/choose", data=bad_body,
                                         headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(bad_req)
            check(False, "an out-of-burst frame should be rejected")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400, f"expected 400, got {exc.code}")

        pending = _json.loads(urllib.request.urlopen(base + "/api/tasks").read())
        check(len(pending["tasks"]) == len(tasks) - 1,
              "a decided burst should drop out of the queue")
    finally:
        server.shutdown()
        server.server_close()

    check((tmp / "d.jsonl").exists(), "decisions should be on disk immediately")
    summary = render_summary(log.stats())
    check("Burst labelling results" in summary, "summary should render")
    check("Preference pairs generated" in summary, "summary should count pairs")


def test_label_summary_thresholds():
    from mck.labeler import render_summary

    low = render_summary({"decided": 100, "skipped": 0, "comparable": 100,
                          "agreed": 50, "self_consistency": 0.5,
                          "preference_pairs": 300})
    check("closer to arbitrary" in low, "a low score should say what it means")

    mid = render_summary({"decided": 100, "skipped": 0, "comparable": 100,
                          "agreed": 72, "self_consistency": 0.72,
                          "preference_pairs": 300})
    check("human parity" in mid, "a mid score should set the target")

    high = render_summary({"decided": 100, "skipped": 0, "comparable": 100,
                           "agreed": 88, "self_consistency": 0.88,
                           "preference_pairs": 300})
    check("consistent rule" in high, "a high score should say so")

    none = render_summary({"decided": 5, "skipped": 0, "comparable": 0, "agreed": 0,
                           "self_consistency": None, "preference_pairs": 10})
    check("could not be measured" in none, "no comparable bursts should be explained")


def test_label_archive_discovery(tmp: Path):
    """Weddings are found by containing raws, not by folder naming."""
    from mck.labeler import find_wedding_folders, render_survey_labelling, survey_labelling

    archive = tmp / "Beast"
    make_wedding(archive / "2025-06-14 Smith", seed=141)
    # Deliberately unconventional naming, and nested a level deeper.
    odd = archive / "2024-09-21 Alvarez" / "CR3 Files" / "Card A"
    odd.mkdir(parents=True)
    (odd / "IMG_1.CR2").write_bytes(b"x")
    (odd / "IMG_2.CR2").write_bytes(b"x")
    # A folder with no raws at all must not be mistaken for a wedding.
    (archive / "Invoices").mkdir()
    (archive / "Invoices" / "note.txt").write_text("not a wedding", encoding="utf-8")

    found = find_wedding_folders(archive)
    names = {w["name"] for w in found}
    check(names == {"2025-06-14 Smith", "2024-09-21 Alvarez"},
          f"discovery should ignore naming and skip non-weddings, got {names}")

    alvarez = next(w for w in found if w["name"] == "2024-09-21 Alvarez")
    check(alvarez["raw_count"] == 2, f"should count nested raws, got {alvarez['raw_count']}")
    check(len(alvarez["raw_folders"]) == 1, "should record the folder holding them")

    smith = next(w for w in found if w["name"] == "2025-06-14 Smith")
    check(smith["raw_count"] > 100, f"fixture wedding should be large, got {smith['raw_count']}")

    survey = survey_labelling(found, mode="all", prefer_exiftool=False,
                              log=lambda *_: None)
    check(survey["total_weddings"] == 2, "both weddings should be surveyed")
    check(survey["total_bursts"] > 0, "the fixture should contribute bursts")
    check(survey["total_pairs"] > 0, "and preference pairs")
    check(survey["total_frames"] == smith["raw_count"] + 2, "frame counts should sum")

    check(survey["sampled"] is False, "a full survey is not a sample")
    check(survey["surveyed"] == 2 and survey["available"] == 2, "counts should agree")

    sample = survey_labelling(found, mode="all", prefer_exiftool=False, limit=1,
                              log=lambda *_: None)
    check(sample["sampled"] is True, "a limited survey should mark itself sampled")
    check(sample["surveyed"] == 1, "only one wedding should be counted")
    sample_text = render_survey_labelling(sample)
    check("Scaling by" in sample_text, "a sample should extrapolate, and say it is doing so")

    text = render_survey_labelling(survey)
    check("Preference pairs available" in text, "survey should headline the pair count")
    check("hours" in text, "survey should estimate the effort")
    check("2025-06-14 Smith" in text, "per-wedding rows should appear")

    check(find_wedding_folders(tmp / "nope") == [], "a missing archive yields nothing")

    # Weddings nested under a container must be reachable, and a depth that is
    # too shallow must make the container vanish rather than look empty.
    deep = tmp / "Beast2"
    buried = deep / "Weddings" / "2024" / "Patterson" / "RAW"
    buried.mkdir(parents=True)
    for n in range(3):
        (buried / f"IMG_{n}.CR2").write_bytes(b"x")

    shallow = find_wedding_folders(deep, max_depth=1)
    check(shallow == [], "too shallow a scan should find nothing, not an empty container")
    deeper = find_wedding_folders(deep, max_depth=4)
    check(len(deeper) == 1 and deeper[0]["raw_count"] == 3,
          f"a deeper scan should reach the buried raws, got {deeper}")


def test_survey_samples_the_largest(tmp: Path):
    """Sampling must follow frame counts, not alphabetical order."""
    from mck.labeler import render_survey_labelling, survey_labelling

    archive = tmp / "Beast"
    make_wedding(archive / "zzz Big Wedding", seed=151)
    for name in ("aaa Tiny", "bbb Tiny", "ccc Tiny"):
        folder = archive / name / "RAW"
        folder.mkdir(parents=True)
        (folder / "IMG_1.CR2").write_bytes(b"x")

    from mck.labeler import find_wedding_folders
    found = find_wedding_folders(archive)
    survey = survey_labelling(found, mode="all", prefer_exiftool=False, limit=1,
                              log=lambda *_: None)
    check(survey["surveyed"] == 1, "one wedding should be counted")
    check(survey["weddings"][0]["name"] == "zzz Big Wedding",
          f"the largest should be sampled, got {survey['weddings'][0]['name']}")
    check(len(survey["not_counted"]) == 3, "the rest should be named, not just counted")

    text = render_survey_labelling(survey)
    check("Not counted:" in text, "the report should name what it skipped")
    check("aaa Tiny" in text, "including the folder names")


def test_label_cli_archive(tmp: Path):
    from mck.cli import main

    archive = tmp / "Beast"
    make_wedding(archive / "2025-06-14 Smith", seed=143)
    out = tmp / "labels" / "decisions.jsonl"

    code = main(["label", "--archive", str(archive), "--out", str(out),
                 "--mode", "all", "--no-exiftool"])
    check(code == 0, f"survey mode should exit 0, got {code}")
    check((tmp / "labels" / "LABELLING-SURVEY.md").exists(),
          "survey should be written next to the decision log")

    # A name that matches nothing should fail with the list, not hang.
    code = main(["label", "--archive", str(archive), "--out", str(out),
                 "--wedding", "nonexistent", "--no-exiftool"])
    check(code == 2, f"an unmatched --wedding should exit 2, got {code}")

    code = main(["label", "--archive", str(tmp / "missing"), "--out", str(out)])
    check(code == 2, "a missing archive should exit 2")

    code = main(["label", "--out", str(out)])
    check(code == 2, "neither --raw nor --archive should exit 2")


def test_recommend_first_sitting():
    """The survey should name the wedding to start with, and justify it."""
    from datetime import date, timedelta
    from mck.labeler import recommend_first_sitting, render_survey_labelling

    old = (date.today() - timedelta(days=500)).isoformat()
    recent = (date.today() - timedelta(days=20)).isoformat()

    rows = [
        # Too few recorded picks to measure anything.
        {"name": "Tiny", "frames": 200, "bursts": 10, "pairs": 20,
         "with_recorded_pick": 5, "shot_on": old},
        # Right size, but shot last month: would measure memory, not taste.
        {"name": "Recent", "frames": 4000, "bursts": 200, "pairs": 700,
         "with_recorded_pick": 200, "shot_on": recent},
        # Old and comfortably sized: the right answer.
        {"name": "Patterson", "frames": 4200, "bursts": 210, "pairs": 760,
         "with_recorded_pick": 210, "shot_on": old},
        {"name": "Broken", "frames": 100, "error": "unreadable", "bursts": 0,
         "pairs": 0},
    ]

    pick = recommend_first_sitting(rows)
    check(pick is not None, "a recommendation should be made")
    check(pick["name"] == "Patterson", f"wrong pick: {pick['name']}")
    check(any("months ago" in r for r in pick["why"]), f"age should be cited: {pick['why']}")
    check(any("hour of clicking" in r for r in pick["why"]),
          f"size should be cited: {pick['why']}")

    # Nothing usable should say so rather than recommending junk.
    check(recommend_first_sitting([rows[0], rows[3]]) is None,
          "too-few-picks weddings should yield no recommendation")
    check(recommend_first_sitting([]) is None, "an empty survey yields nothing")

    # A malformed date must not crash the scorer.
    odd = recommend_first_sitting([{**rows[2], "shot_on": "not-a-date"}])
    check(odd is not None, "an unparseable date should still be recommendable")
    check(any("age could not be checked" in r for r in odd["why"]),
          f"and should say why: {odd['why']}")

    text = render_survey_labelling({
        "mode": "decisive", "weddings": rows, "total_weddings": 4,
        "total_frames": 8500, "total_bursts": 420, "total_pairs": 1480,
        "total_with_recorded_pick": 415, "archive": "/Volumes/The Beast",
    })
    check("Start with this one" in text, "the report should lead with the pick")
    check("--wedding \"Patterson\"" in text, "and give a pasteable command")
    check("/Volumes/The Beast" in text, "using the real archive path")
    check("Every wedding counted" in text, "the full table should still be there")

    none_text = render_survey_labelling({
        "mode": "decisive", "weddings": [rows[0]], "total_weddings": 1,
        "total_frames": 200, "total_bursts": 10, "total_pairs": 20,
        "total_with_recorded_pick": 5,
    })
    check("No wedding is a good first sitting" in none_text,
          "and should explain when nothing qualifies")
    check("--burst-gap" in none_text, "suggesting what to try next")


def test_tilde_prefixed_folder_names(tmp: Path):
    """Folders named ~Something must not be read as a username."""
    from mck.cli import _path, main

    # The real failure: Path("~Wedding Catalog").expanduser() raises RuntimeError.
    check(str(_path("~Wedding Catalog")) == "~Wedding Catalog",
          "a ~-prefixed folder name must survive unchanged")
    check(str(_path("/Volumes/The Beast/~Wedding Catalog"))
          == "/Volumes/The Beast/~Wedding Catalog",
          "a ~ inside an absolute path must survive unchanged")
    check(str(_path("~/Sandbox")).endswith("/Sandbox"), "~/ must still expand to home")
    check(_path("~") == Path("~").expanduser(), "a bare ~ must still expand")

    # End to end: an archive whose folders all start with ~, like the real drive.
    archive = tmp / "Beast"
    make_wedding(archive / "~Wedding Catalog", seed=161)
    out = tmp / "labels" / "decisions.jsonl"
    code = main(["label", "--archive", str(archive), "--out", str(out),
                 "--mode", "all", "--no-exiftool"])
    check(code == 0, f"a ~-named wedding folder should survey cleanly, got {code}")

    text = (tmp / "labels" / "LABELLING-SURVEY.md").read_text(encoding="utf-8")
    check("~Wedding Catalog" in text, "and appear in the report by name")

    code = main(["label", "--archive", str(archive), "--out", str(out),
                 "--wedding", "~Wedding Catalog", "--summary", "--no-exiftool"])
    check(code == 0, f"selecting it by its ~ name should work, got {code}")


def test_port_already_in_use(tmp: Path):
    """A busy port should move up, not raise a traceback."""
    from http.server import ThreadingHTTPServer
    from mck.labeler import _Handler, bind_server

    # Hold a port the way a leftover labelling server would.
    blocker = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    taken = blocker.server_address[1]
    try:
        server = bind_server(taken)
        try:
            check(server.server_address[1] != taken,
                  "a busy port should be stepped over")
            check(taken < server.server_address[1] <= taken + 20,
                  f"and the next free one used, got {server.server_address[1]}")
        finally:
            server.server_close()

        # No room at all should explain itself, not raise a bare OSError.
        try:
            bind_server(taken, attempts=1)
            check(False, "an exhausted range should raise")
        except OSError as exc:
            check("lsof" in str(exc),
                  f"the error should name the fix, got: {exc}")
    finally:
        blocker.server_close()


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
        test_cli, test_catalog_label_discovery, test_catalog_missing_tables_degrade,
        test_scan_with_catalog_labels, test_scan_cli_requires_a_label,
        test_xmp_write_preserves_everything_else, test_xmp_write_element_form_and_creation,
        test_cull_automator, test_cull_waits_for_copy_to_finish, test_cull_write_mode_backs_up,
        test_jpeg_dimensions, test_editorial_ready, test_editorial_blocks_on_spec_and_count,
        test_editorial_exclusivity, test_editorial_cli,
        test_discover_survey, test_discover_cli,
        test_bootstrap, test_bootstrap_cli, test_star_taxonomy,
        test_preview_extraction, test_label_task_building, test_decision_log,
        test_label_server_round_trip, test_label_summary_thresholds,
        test_port_already_in_use,
        test_label_archive_discovery, test_survey_samples_the_largest,
        test_label_cli_archive,
        test_recommend_first_sitting, test_tilde_prefixed_folder_names,
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
