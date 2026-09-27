"""Viability analysis and report generation.

The report is built around a small number of numbers that decide whether the
whole project is worth building:

* **Keep rate and join rate** — is there a clean label at all?
* **Rating AUC** — do the star ratings in the archive actually predict what got
  delivered? If they do, ratings are a second, larger label source. If they
  don't, ratings are a tool's suggestion and must not be trained on.
* **Decisive bursts and preference pairs** — how much of the cull is near-
  duplicate resolution, and how many training pairs that yields.
* **Sidecar write sessions** — whether any before/after correction history
  survives, or only final state.
* **Develop-setting entropy** — whether the edits in the archive carry personal
  style or are one preset applied wholesale.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .scan import ImageRecord
from .stats import auc, cluster_timestamps, contingency, describe, entropy
from .xmp import DEVELOP_FIELDS


# McKinley's star taxonomy, confirmed September 2026. Stars 1-3 are *reason
# codes* for rejection, not a quality ordering: a 3-star duplicate is often a
# perfectly good frame that lost to a near-identical sibling, and sits below a
# 2-star blurry frame only by accident of the scale. Stars 4-5 are an actual
# quality scale over survivors.
RATING_MEANING = {
    0: "unrated",
    1: "rejected",
    2: "blurry / technical",
    3: "duplicate, lost to a sibling",
    4: "keeper",
    5: "highlight, website-worthy",
}
KEEP_STAR = 4          # 4 and 5 are delivered
DUPLICATE_STAR = 3     # the losing frame of a near-identical set
BLUR_STAR = 2
HIGHLIGHT_STAR = 5


def _taxonomy(records: list[ImageRecord]) -> dict:
    """Read the star ratings as the reason codes they actually are.

    Three things this measures that an ordinal reading cannot:

    * **Annotated preference pairs.** A 3 sharing a burst with a 4 or 5 is an
      explicit "I preferred that one over this one" under near-identical
      conditions, recorded by hand at cull time. These do not need inferring.
    * **Duplicate-code consistency.** If frames marked 3 rarely sit in a burst
      with a winner, either the bursts are being detected wrongly or the 3 is
      being used for something other than duplicates.
    * **Free technical labels.** Every 2 is a labelled blur example.
    """
    by_star: dict[int, dict] = {}
    for r in records:
        star = r.rating
        if star is None:
            continue
        bucket = by_star.setdefault(star, {"n": 0, "delivered": 0})
        bucket["n"] += 1
        if r.delivered:
            bucket["delivered"] += 1
    for star, bucket in by_star.items():
        bucket["meaning"] = RATING_MEANING.get(star, "unknown")
        bucket["keep_rate"] = round(bucket["delivered"] / bucket["n"], 4) if bucket["n"] else 0.0

    bursts: dict[int, list[ImageRecord]] = {}
    for r in records:
        if r.burst_id is not None:
            bursts.setdefault(r.burst_id, []).append(r)

    annotated_pairs = 0
    threes_total = threes_with_winner = 0
    for members in bursts.values():
        winners = [m for m in members if (m.rating or 0) >= KEEP_STAR]
        losers = [m for m in members if m.rating == DUPLICATE_STAR]
        annotated_pairs += len(winners) * len(losers)
        threes_total += len(losers)
        if winners:
            threes_with_winner += len(losers)

    # How well does "4 or better" agree with what the catalog says was delivered?
    # Precision and recall are the right frame here, not AUC: the star rule is a
    # threshold decision, and 1-3 have no meaningful internal ordering to rank.
    tp = sum(1 for r in records if (r.rating or 0) >= KEEP_STAR and r.delivered)
    fp = sum(1 for r in records if (r.rating or 0) >= KEEP_STAR and not r.delivered)
    fn = sum(1 for r in records if (r.rating or 0) < KEEP_STAR and r.delivered)
    rated = [r for r in records if r.rating is not None]

    colors = Counter(r.label for r in records if r.label)
    colors_delivered = Counter(r.label for r in records if r.label and r.delivered)

    return {
        "by_star": dict(sorted(by_star.items())),
        "annotated_preference_pairs": annotated_pairs,
        "duplicates_total": threes_total,
        "duplicates_in_a_burst_with_a_winner": threes_with_winner,
        "duplicate_consistency": (
            round(threes_with_winner / threes_total, 4) if threes_total else 0.0
        ),
        "blur_labels": by_star.get(BLUR_STAR, {}).get("n", 0),
        "highlights": by_star.get(HIGHLIGHT_STAR, {}).get("n", 0),
        "star_rule": {
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else 0.0,
            "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
            "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "rated_frames": len(rated),
        },
        "colors": {
            c: {"n": n, "delivered": colors_delivered.get(c, 0),
                "keep_rate": round(colors_delivered.get(c, 0) / n, 4) if n else 0.0}
            for c, n in colors.most_common()
        },
    }


def _fmt_epoch(epoch: float | None) -> str:
    """Format as wall-clock time.

    EXIF timestamps are naive local time — what the camera's clock read when the
    shutter fired — and `exif._parse_dt` converts them with the same local
    interpretation. Formatting them back as local time round-trips to the clock
    time McKinley was actually shooting at. Formatting as UTC would shift every
    displayed time by the machine's offset.
    """
    if epoch is None:
        return "n/a"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _fmt_clock(epoch: float | None) -> str:
    if epoch is None:
        return "n/a"
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _scene_table(records: list[ImageRecord]) -> list[dict]:
    """Per-scene rows: when it ran, how much was shot, how much survived.

    Scenes are cut at long gaps between frames, so they approximate the phases
    of the day — getting ready, ceremony, portraits, reception — without any
    manual labelling.
    """
    scenes: dict[int, list[ImageRecord]] = {}
    for r in records:
        if r.scene_id is not None and r.exif.capture_epoch is not None:
            scenes.setdefault(r.scene_id, []).append(r)

    rows = []
    for members in scenes.values():
        epochs = sorted(m.exif.capture_epoch for m in members)
        kept = sum(1 for m in members if m.delivered)
        flash = sum(1 for m in members if m.exif.flash_fired)
        isos = [m.exif.iso for m in members if m.exif.iso]
        rows.append({
            "start": epochs[0],
            "start_clock": _fmt_clock(epochs[0]),
            "duration": _duration(epochs[-1] - epochs[0]),
            "frames": len(members),
            "delivered": kept,
            "keep_rate": round(kept / len(members), 4),
            "flash_share": round(flash / len(members), 4),
            "median_iso": sorted(isos)[len(isos) // 2] if isos else None,
            "bodies": len({m.exif.body_key for m in members}),
        })
    rows.sort(key=lambda r: r["start"])
    for i, row in enumerate(rows, 1):
        row["scene"] = i
    return rows


def _hour_table(records: list[ImageRecord]) -> list[dict]:
    """Frames and keep rate by hour of the shooting day."""
    buckets: dict[int, list[ImageRecord]] = {}
    for r in records:
        if r.exif.capture_epoch is None:
            continue
        hour = datetime.fromtimestamp(r.exif.capture_epoch).hour
        buckets.setdefault(hour, []).append(r)

    rows = []
    for hour in sorted(buckets):
        members = buckets[hour]
        kept = sum(1 for m in members if m.delivered)
        rows.append({
            "hour": hour,
            "label": f"{hour:02d}:00",
            "frames": len(members),
            "delivered": kept,
            "keep_rate": round(kept / len(members), 4),
        })
    return rows


def analyze(records: list[ImageRecord], meta: dict) -> dict:
    """Compute every metric the report renders. Pure — no I/O."""
    n = len(records)
    kept = [r.delivered for r in records]
    n_kept = sum(kept)

    with_xmp = [r for r in records if r.xmp is not None]
    with_time = [r for r in records if r.exif.capture_epoch is not None]

    ratings = [r.rating for r in records]
    rated = [r for r in ratings if r is not None]

    # Does the star rating predict delivery?
    rating_auc = auc([float(r) if r is not None else None for r in ratings], kept)
    label_keep = contingency([r.label for r in records], kept)
    rating_keep = contingency(ratings, kept)

    # Who wrote the sidecars, and when.
    writers = Counter(r.writer for r in records)
    mtimes = [r.xmp_mtime for r in records if r.xmp_mtime is not None]
    write_sessions = cluster_timestamps(mtimes)

    # Develop settings: coverage and whether they vary at all.
    dev_records = [r for r in with_xmp if r.xmp.has_develop]
    nontrivial = [r for r in with_xmp if r.xmp.has_nontrivial_develop]
    dev_stats = {}
    for field_name in DEVELOP_FIELDS:
        vals = [r.xmp.get_float(field_name) for r in with_xmp]
        d = describe([v for v in vals if v is not None])
        if d.get("n"):
            dev_stats[field_name] = d

    # A style archive should show many distinct develop fingerprints. One
    # dominant fingerprint means a preset was batch-applied and there is no
    # per-image styling decision to learn.
    fingerprints = Counter()
    for r in with_xmp:
        vals = r.xmp.develop_values()
        if vals:
            fingerprints[tuple(round(v, 3) for _, v in sorted(vals.items()))] += 1
    fp_entropy = entropy(list(fingerprints.values()))
    top_fp_share = (fingerprints.most_common(1)[0][1] / sum(fingerprints.values())) if fingerprints else 0.0

    # Timeline
    epochs = sorted(r.exif.capture_epoch for r in with_time)
    span_hours = (epochs[-1] - epochs[0]) / 3600.0 if len(epochs) > 1 else 0.0

    scene_keep = contingency([r.scene_id for r in records], kept)
    scene_rates = [v["keep_rate"] for v in scene_keep.values() if v["n"] >= 20]
    scene_spread = (max(scene_rates) - min(scene_rates)) if len(scene_rates) > 1 else 0.0

    bursts = meta.get("bursts", {})
    decisive = bursts.get("decisive", 0)
    multi = bursts.get("multi_frame", 0)

    # Of everything dropped, how much was dropped inside a burst that also had a
    # keeper — i.e. genuine near-duplicate resolution rather than outright rejection.
    dropped_in_decisive = 0
    burst_members: dict[int, list[ImageRecord]] = {}
    for r in records:
        if r.burst_id is not None:
            burst_members.setdefault(r.burst_id, []).append(r)
    for members in burst_members.values():
        k = sum(1 for m in members if m.delivered)
        if 0 < k < len(members):
            dropped_in_decisive += len(members) - k

    n_dropped = n - n_kept

    return {
        "counts": {
            "source_images": n,
            "delivered_matched": n_kept,
            "dropped": n_dropped,
            "keep_rate": round(n_kept / n, 4) if n else 0.0,
            "with_sidecar": len(with_xmp),
            "sidecar_coverage": round(len(with_xmp) / n, 4) if n else 0.0,
            "with_capture_time": len(with_time),
            "capture_time_coverage": round(len(with_time) / n, 4) if n else 0.0,
            "rated": len(rated),
            "rating_coverage": round(len(rated) / n, 4) if n else 0.0,
        },
        "join": meta.get("join", {}),
        "taxonomy": _taxonomy(records),
        "labels": {
            "rating_auc": round(rating_auc, 4) if rating_auc is not None else None,
            "rating_distribution": rating_keep,
            "color_label_distribution": label_keep,
        },
        "sidecars": {
            "writers": dict(writers),
            "write_sessions": [
                {"start": _fmt_epoch(t), "files": c} for t, c in write_sessions[:20]
            ],
            "write_session_count": len(write_sessions),
            "parse_errors": sum(1 for r in with_xmp if r.xmp.parse_error),
        },
        "develop": {
            "with_any_develop": len(dev_records),
            "with_nontrivial_develop": len(nontrivial),
            "nontrivial_share": round(len(nontrivial) / n, 4) if n else 0.0,
            "distinct_fingerprints": len(fingerprints),
            "fingerprint_entropy_bits": round(fp_entropy, 3),
            "top_fingerprint_share": round(top_fp_share, 4),
            "fields": dev_stats,
            "with_masks": sum(1 for r in with_xmp if (r.xmp.mask_count or 0) > 0),
            "grayscale": sum(1 for r in with_xmp if r.xmp.converted_to_grayscale),
        },
        "timeline": {
            "span_hours": round(span_hours, 2),
            "first_frame": _fmt_epoch(epochs[0] if epochs else None),
            "last_frame": _fmt_epoch(epochs[-1] if epochs else None),
            "bodies": len({r.exif.body_key for r in with_time}),
            "scene_count": meta.get("scenes", {}).get("count", 0),
            "scene_keep_rate_spread": round(scene_spread, 4),
            "scenes": _scene_table(records),
            "hours": _hour_table(records),
            "frames_per_hour": (
                round(len(with_time) / span_hours) if span_hours > 0 else 0
            ),
        },
        "bursts": bursts,
        "duplicate_work": {
            "multi_frame_bursts": multi,
            "decisive_bursts": decisive,
            "decisive_share_of_multi": round(decisive / multi, 4) if multi else 0.0,
            "dropped_inside_decisive_bursts": dropped_in_decisive,
            "share_of_drops_that_are_near_dupes": (
                round(dropped_in_decisive / n_dropped, 4) if n_dropped else 0.0
            ),
        },
    }


def verdicts(a: dict) -> list[dict]:
    """Turn the metrics into explicit pass/warn/fail findings."""
    out: list[dict] = []
    c = a["counts"]

    def add(status, title, detail):
        out.append({"status": status, "title": title, "detail": detail})

    # 1. Is there a label?
    if c["delivered_matched"] == 0:
        add("FAIL", "No delivered set matched",
            "Nothing joined the source frames to a delivered gallery, so this wedding "
            "carries no ground-truth keep/drop label. Point --delivered at the final "
            "gallery folder, or confirm the exports still exist.")
    elif c["keep_rate"] > 0.6:
        add("WARN", f"Keep rate {c['keep_rate']:.0%} is high",
            "This usually means the delivered folder is a subset re-export or the join "
            "over-matched. A wedding cull normally lands between 10% and 30%.")
    elif c["keep_rate"] < 0.03:
        add("WARN", f"Keep rate {c['keep_rate']:.0%} is very low",
            "Likely a partial delivered folder (highlights only) rather than the full gallery.")
    else:
        add("PASS", f"Clean keep/drop label on {c['source_images']:,} frames",
            f"{c['delivered_matched']:,} delivered / {c['dropped']:,} dropped "
            f"({c['keep_rate']:.1%} keep rate). This is the training label and it needs "
            "no cooperation from any third-party tool.")

    # 2. Join quality
    j = a["join"]
    matched = (j.get("matched_by_stem", 0) + j.get("matched_by_time", 0)
               + j.get("matched_by_catalog", 0))
    if j.get("matched_by_catalog"):
        rate = matched / j["delivered_files"] if j.get("delivered_files") else 0.0
        if rate < 0.85:
            add("WARN", f"Catalog label matched only {rate:.0%} of its images to files on disk",
                f"{j.get('unmatched', 0):,} images the catalog marked as chosen had no matching "
                "raw file in the scanned folders. Usually the folder filter is too narrow, or "
                "the raws were moved after the catalog last saw them.")
        else:
            add("PASS", f"Catalog label matched {rate:.0%} of its images to files on disk",
                f"{j.get('matched_by_catalog', 0):,} frames joined by filename stem. The "
                "delivered gallery never had to exist locally.")
    elif j.get("delivered_files"):
        rate = matched / j["delivered_files"]
        if rate < 0.9:
            add("WARN", f"Only {rate:.0%} of delivered files matched a source frame",
                f"{j.get('unmatched', 0):,} unmatched. Examples: "
                f"{', '.join(j.get('unmatched_examples', [])[:5]) or 'n/a'}. "
                "Renamed exports need capture-time matching, which needs EXIF on both sides.")
        else:
            add("PASS", f"{rate:.0%} of delivered files joined to a source frame",
                f"{j.get('matched_by_stem', 0):,} by filename, {j.get('matched_by_time', 0):,} "
                "by capture timestamp.")

    # 3. Capture-time coverage
    if c["capture_time_coverage"] < 0.95:
        add("WARN", f"Capture time on only {c['capture_time_coverage']:.0%} of frames",
            "Burst detection, timeline segmentation and renamed-export matching all depend "
            "on this. Installing exiftool usually takes coverage to ~100%.")
    else:
        add("PASS", f"Capture time on {c['capture_time_coverage']:.0%} of frames",
            "Enough for burst grouping and phase segmentation.")

    # 4. Are ratings a usable label?
    r_auc = a["labels"]["rating_auc"]
    if r_auc is None:
        add("INFO", "No star ratings present",
            "Not a problem — the delivered-set join is the stronger label anyway.")
    elif r_auc >= 0.85:
        add("PASS", f"Star ratings predict delivery (AUC {r_auc:.2f})",
            "Ratings track your final decisions closely, so rated-but-undelivered weddings "
            "can be used as extra training data.")
    elif r_auc >= 0.65:
        add("WARN", f"Star ratings only partly predict delivery (AUC {r_auc:.2f})",
            "Ratings are a weak proxy. Train on the delivered-set label and treat ratings "
            "as a feature at most.")
    else:
        add("FAIL", f"Star ratings do not predict delivery (AUC {r_auc:.2f})",
            "The ratings in this archive are close to noise with respect to what you "
            "actually delivered. Do not train on them.")

    # 4b. The star taxonomy, read as reason codes.
    t = a["taxonomy"]
    pairs = t["annotated_preference_pairs"]
    if pairs:
        add("PASS", f"{pairs:,} hand-annotated preference pairs",
            f"{t['duplicates_in_a_burst_with_a_winner']:,} frames marked 3 (duplicate) sit in a "
            "burst alongside a 4 or 5. Each is an explicit 'I picked that one over this one' "
            "under near-identical conditions, recorded at cull time. This is the signal that "
            "makes a personalized ranker learnable, and it did not have to be inferred.")
    if t["duplicates_total"]:
        consistency = t["duplicate_consistency"]
        if consistency < 0.6:
            add("WARN", f"Only {consistency:.0%} of 3-star frames sit in a burst with a winner",
                f"{t['duplicates_total']:,} frames are marked 3 (duplicate) but most have no "
                "higher-rated sibling nearby. Either the burst window needs widening "
                "(try --burst-gap 4), or 3 is being used for something other than duplicates.")
        else:
            add("PASS", f"{consistency:.0%} of 3-star duplicates sit in a burst with a winner",
                "The duplicate code and the timestamp-derived bursts agree, which means both "
                "are trustworthy.")
    if t["blur_labels"]:
        add("INFO", f"{t['blur_labels']:,} frames labelled blurry (2 star)",
            "A free, hand-labelled training set for technical-quality rejection. That is "
            "normally the part you would have to label by hand.")
    if t["highlights"]:
        add("INFO", f"{t['highlights']:,} frames marked 5 star (highlight)",
            "The most personal judgement in the taxonomy, and the natural label for an "
            "editorial-grade scorer later.")

    sr = t["star_rule"]
    if sr["rated_frames"] and (sr["true_positive"] + sr["false_positive"]):
        add("INFO", f"'4 or better' matches delivery with precision {sr['precision']:.0%}, "
            f"recall {sr['recall']:.0%}",
            f"{sr['false_positive']:,} frames rated 4+ were not delivered and "
            f"{sr['false_negative']:,} delivered frames were rated below 4. A gap in either "
            "direction is where the star pass and the final cut diverge, which is itself "
            "worth training on.")

    if t["colors"]:
        add("INFO", f"Colour labels in use on {sum(c['n'] for c in t['colors'].values()):,} frames",
            "Current usage: " + ", ".join(f"{k} {v['n']:,}" for k, v in t["colors"].items())
            + ". Worth knowing before repurposing the colour axis.")

    # 5. Correction history
    s = a["sidecars"]
    if s["write_session_count"] <= 1:
        add("WARN", "No correction history in the sidecars",
            "Every sidecar was written in a single session, so the archive holds only the "
            "final state. The 'where I disagreed with the AI' signal is not recoverable "
            "retroactively — an XMP keeps one state, and the correction overwrote the "
            "suggestion. This does not block the culling model, which learns from the "
            "delivered set. It does mean that dataset has to be captured going forward: "
            "`mck snapshot --tag post-ai` before you review, `--tag post-review` after.")
    else:
        add("PASS", f"{s['write_session_count']} distinct sidecar write sessions",
            "Multiple write passes suggest before/after states may be partially "
            "reconstructable. Worth checking whether your backups hold an earlier copy.")

    # 6. Where the cull work actually is
    d = a["duplicate_work"]
    if d["share_of_drops_that_are_near_dupes"] >= 0.3:
        add("INFO", f"{d['share_of_drops_that_are_near_dupes']:.0%} of drops are near-duplicate resolution",
            f"{d['decisive_bursts']:,} bursts contained both a keeper and a reject. That is "
            "the part of the job a generic 'good photo' model cannot do, and it is where "
            "your data is uniquely valuable.")
    else:
        add("INFO", f"Only {d['share_of_drops_that_are_near_dupes']:.0%} of drops are near-duplicate resolution",
            "Most rejections are outright, which is the easier half of the problem — "
            "technical-quality filtering will capture much of the value.")

    pairs = a["bursts"].get("preference_pairs", 0)
    add("INFO", f"{pairs:,} within-burst preference pairs from this wedding alone",
        "Each is an ordered 'you preferred A over B' example under near-identical "
        "conditions. Across 100 weddings this is the dense signal that makes a "
        "personalized ranker learnable.")

    # 7. Style signal
    dv = a["develop"]
    if dv["with_nontrivial_develop"] == 0:
        add("WARN", "No non-default develop settings found",
            "Nothing here teaches an editing model. Either the edits live only in the "
            "Lightroom catalog (sidecars were never written) or this folder predates editing.")
    elif dv["top_fingerprint_share"] > 0.8:
        add("WARN", f"{dv['top_fingerprint_share']:.0%} of edited frames share one identical setting fingerprint",
            "That is a batch-applied preset, not per-image styling. Editing prediction "
            "trained on this would learn to reproduce one preset.")
    else:
        add("PASS", f"{dv['distinct_fingerprints']:,} distinct develop fingerprints "
            f"({dv['fingerprint_entropy_bits']:.1f} bits)",
            "Per-image editing decisions vary, so there is style signal here — though see "
            "the feasibility note on who authored these edits before training on them.")

    # 8. Context dependence
    t = a["timeline"]
    if t["scene_keep_rate_spread"] >= 0.15:
        add("INFO", f"Keep rate varies {t['scene_keep_rate_spread']:.0%} across scenes",
            "Your selectivity depends heavily on what part of the day it is. A model that "
            "ignores context will systematically over-deliver some phases and under-deliver "
            "others — scene features are not optional.")

    return out


def render_markdown(a: dict, findings: list[dict], meta: dict, title: str) -> str:
    """Human-readable audit report."""
    c = a["counts"]
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}
    lines: list[str] = []
    w = lines.append

    w(f"# Phase 0 Data Audit — {title}")
    w("")
    w(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    w("")

    fails = sum(1 for f in findings if f["status"] == "FAIL")
    warns = sum(1 for f in findings if f["status"] == "WARN")
    pairs = a["bursts"].get("preference_pairs", 0)

    if fails:
        headline = (
            f"**Not usable as training data** — {fails} blocking issue(s). "
            "Fix these before counting this wedding toward the dataset."
        )
    elif warns:
        headline = (
            f"**Usable, with {warns} caveat(s).** The keep/drop label is sound; "
            "the warnings below constrain what else can be learned from this wedding."
        )
    else:
        headline = "**Usable.** Clean label, full timing coverage, and structure to learn from."

    w(f"## Verdict\n\n{headline}\n")
    w(f"This wedding contributes **{c['delivered_matched']:,} positive** and "
      f"**{c['dropped']:,} negative** examples, plus **{pairs:,}** within-burst "
      "preference pairs.\n")

    w("## Findings\n")
    for f in findings:
        w(f"### {icon.get(f['status'], '•')} {f['title']}")
        w("")
        w(f["detail"])
        w("")

    w("## Inventory\n")
    w("| Metric | Value |")
    w("| --- | --- |")
    w(f"| Source frames | {c['source_images']:,} |")
    w(f"| Delivered (matched) | {c['delivered_matched']:,} |")
    w(f"| Dropped | {c['dropped']:,} |")
    w(f"| **Keep rate** | **{c['keep_rate']:.1%}** |")
    w(f"| XMP sidecar coverage | {c['sidecar_coverage']:.1%} ({c['with_sidecar']:,}) |")
    w(f"| Capture-time coverage | {c['capture_time_coverage']:.1%} ({c['with_capture_time']:,}) |")
    w(f"| Star-rating coverage | {c['rating_coverage']:.1%} ({c['rated']:,}) |")
    w("")

    j = a["join"]
    w("## Delivered-set join\n")
    w("| Method | Files |")
    w("| --- | --- |")
    w(f"| Delivered files found | {j.get('delivered_files', 0):,} |")
    w(f"| Matched by filename stem | {j.get('matched_by_stem', 0):,} |")
    w(f"| Matched by capture time | {j.get('matched_by_time', 0):,} |")
    if j.get("matched_by_catalog"):
        w(f"| Matched from Lightroom catalog | {j.get('matched_by_catalog', 0):,} |")
    w(f"| Unmatched | {j.get('unmatched', 0):,} |")
    w(f"| Ambiguous timestamp matches | {j.get('ambiguous_time', 0):,} |")
    w("")

    t = a["timeline"]
    w("## Timeline\n")
    w(f"- Span: **{t['span_hours']:.1f} h** ({t['first_frame']} → {t['last_frame']}, camera clock)")
    w(f"- Shooting rate: **{t['frames_per_hour']:,} frames/hour** averaged across the day")
    w(f"- Camera bodies detected: **{t['bodies']}**")
    w(f"- Scenes (gaps > {meta.get('scene_gap', 420) / 60:.0f} min): **{t['scene_count']}**")
    w(f"- Keep-rate spread across scenes: **{t['scene_keep_rate_spread']:.1%}**")
    w("")

    if t["scenes"]:
        w("### Scenes\n")
        w("Cut at gaps in shooting, so these approximate the phases of the day "
          "without any manual labelling.\n")
        w("| # | Start | Duration | Bodies | Frames | Delivered | Keep rate | Flash | Median ISO |")
        w("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in t["scenes"]:
            iso = f"{row['median_iso']:,}" if row["median_iso"] else "—"
            w(f"| {row['scene']} | {row['start_clock']} | {row['duration']} | "
              f"{row['bodies']} | {row['frames']:,} | {row['delivered']:,} | "
              f"{row['keep_rate']:.1%} | {row['flash_share']:.0%} | {iso} |")
        w("")

    if t["hours"]:
        w("### By hour of day\n")
        peak = max(t["hours"], key=lambda r: r["frames"])
        w("| Hour | Frames | Delivered | Keep rate | |")
        w("| --- | --- | --- | --- | --- |")
        for row in t["hours"]:
            bar = "█" * max(1, round(20 * row["frames"] / peak["frames"]))
            w(f"| {row['label']} | {row['frames']:,} | {row['delivered']:,} | "
              f"{row['keep_rate']:.1%} | `{bar}` |")
        w("")

    b = a["bursts"]
    d = a["duplicate_work"]
    w("## Burst structure\n")
    w("| Metric | Value |")
    w("| --- | --- |")
    w(f"| Bursts (gap ≤ {meta.get('burst_gap', 2.0)}s) | {b.get('count', 0):,} |")
    w(f"| Single-frame | {b.get('singletons', 0):,} |")
    w(f"| Multi-frame | {b.get('multi_frame', 0):,} |")
    w(f"| Largest burst | {b.get('largest', 0):,} frames |")
    w(f"| Mean burst size | {b.get('mean_size', 0)} |")
    w(f"| Decisive (mixed keep/drop) | {d['decisive_bursts']:,} |")
    w(f"| Kept wholesale | {b.get('wholesale_keep', 0):,} |")
    w(f"| Dropped wholesale | {b.get('wholesale_drop', 0):,} |")
    w(f"| **Preference pairs** | **{b.get('preference_pairs', 0):,}** |")
    w(f"| Drops that are near-dupe resolution | {d['share_of_drops_that_are_near_dupes']:.1%} |")
    w("")

    t = a["taxonomy"]
    if t["by_star"]:
        w("## Star taxonomy\n")
        w("Stars 1-3 are reason codes for rejection, not a quality ordering. "
          "Stars 4-5 rank the survivors.\n")
        w("| Star | Means | Frames | Delivered | Keep rate |")
        w("| --- | --- | --- | --- | --- |")
        for star, v in t["by_star"].items():
            w(f"| {star} | {v['meaning']} | {v['n']:,} | {v['delivered']:,} | "
              f"{v['keep_rate']:.1%} |")
        w("")
        w(f"- **Hand-annotated preference pairs: {t['annotated_preference_pairs']:,}** "
          "(a 3 sharing a burst with a 4 or 5)")
        w(f"- Duplicate-code consistency: **{t['duplicate_consistency']:.1%}** of 3-star frames "
          "have a higher-rated sibling in the same burst")
        w(f"- Blur-labelled frames (2 star): **{t['blur_labels']:,}**")
        w(f"- Highlights (5 star): **{t['highlights']:,}**")
        sr = t["star_rule"]
        w(f"- '4 or better' vs actual delivery: precision **{sr['precision']:.1%}**, "
          f"recall **{sr['recall']:.1%}** "
          f"({sr['false_positive']:,} rated-up-but-not-delivered, "
          f"{sr['false_negative']:,} delivered-but-rated-down)")
        w("")

    if t["colors"]:
        w("## Colour labels, as currently used\n")
        w("| Colour | Frames | Delivered | Keep rate |")
        w("| --- | --- | --- | --- |")
        for colour, v in t["colors"].items():
            w(f"| {colour} | {v['n']:,} | {v['delivered']:,} | {v['keep_rate']:.1%} |")
        w("")

    lab = a["labels"]
    if lab["rating_distribution"]:
        w("## Star rating vs delivery\n")
        w(f"Rating AUC: **{lab['rating_auc'] if lab['rating_auc'] is not None else 'n/a'}** "
          "(0.5 = no information, 1.0 = perfect). Treat this as a rough check only: AUC "
          "assumes the stars are ordered by quality, and 1-3 are reason codes with no "
          "internal ordering. The precision and recall figures above are the honest "
          "measure.\n")
        w("| Rating | Frames | Delivered | Keep rate |")
        w("| --- | --- | --- | --- |")
        for k, v in lab["rating_distribution"].items():
            w(f"| {k} | {v['n']:,} | {v['kept']:,} | {v['keep_rate']:.1%} |")
        w("")

    if lab["color_label_distribution"]:
        w("## Colour label vs delivery\n")
        w("| Label | Frames | Delivered | Keep rate |")
        w("| --- | --- | --- | --- |")
        for k, v in lab["color_label_distribution"].items():
            w(f"| {k} | {v['n']:,} | {v['kept']:,} | {v['keep_rate']:.1%} |")
        w("")

    s = a["sidecars"]
    w("## Sidecar provenance\n")
    w("| Attributed writer | Frames |")
    w("| --- | --- |")
    for k, v in sorted(s["writers"].items(), key=lambda kv: -kv[1]):
        w(f"| {k} | {v:,} |")
    w("")
    w(f"Write sessions detected: **{s['write_session_count']}**")
    if s["write_sessions"]:
        w("")
        for sess in s["write_sessions"][:10]:
            w(f"- {sess['start']} UTC — {sess['files']:,} files")
    w("")
    if s["parse_errors"]:
        w(f"⚠️ {s['parse_errors']} sidecar(s) failed to parse.")
        w("")

    dv = a["develop"]
    w("## Develop settings\n")
    w(f"- Sidecars with any develop data: **{dv['with_any_develop']:,}**")
    w(f"- With non-default settings: **{dv['with_nontrivial_develop']:,}** ({dv['nontrivial_share']:.1%})")
    w(f"- Distinct setting fingerprints: **{dv['distinct_fingerprints']:,}** "
      f"(entropy {dv['fingerprint_entropy_bits']:.2f} bits)")
    w(f"- Largest identical group: **{dv['top_fingerprint_share']:.1%}** of edited frames")
    w(f"- With local adjustment masks: **{dv['with_masks']:,}**")
    w(f"- Converted to B&W: **{dv['grayscale']:,}**")
    w("")
    if dv["fields"]:
        w("| Field | n | mean | sd | min | median | max |")
        w("| --- | --- | --- | --- | --- | --- | --- |")
        for name, st in dv["fields"].items():
            w(f"| `{name}` | {st['n']:,} | {st['mean']} | {st['sd']} | "
              f"{st['min']} | {st['median']} | {st['max']} |")
        w("")

    w("---")
    w("")
    w("_Read-only audit: no source photograph or sidecar was modified._")
    return "\n".join(lines)


def write_reports(
    records: list[ImageRecord],
    meta: dict,
    out_dir: Path,
    title: str,
) -> dict:
    """Write inventory.csv, audit.json and AUDIT_REPORT.md. Returns the analysis."""
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    analysis = analyze(records, meta)
    findings = verdicts(analysis)

    rows = [r.to_row() for r in records]
    if rows:
        columns: list[str] = []
        for row in rows:
            for k in row:
                if k not in columns:
                    columns.append(k)
        with (out_dir / "inventory.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    payload = {
        "title": title,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "analysis": analysis,
        "findings": findings,
    }
    (out_dir / "audit.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "AUDIT_REPORT.md").write_text(
        render_markdown(analysis, findings, meta, title), encoding="utf-8"
    )
    return payload
