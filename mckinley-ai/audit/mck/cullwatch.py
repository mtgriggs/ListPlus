"""Culling automator — watch an intake folder, pre-cull each wedding.

This is the workflow half of the culling system. The model that ranks frames by
"would McKinley deliver this" does not exist yet, and this module does not
pretend otherwise: the scorer is an interface with a documented baseline, and
the learned model drops into the same slot at Phase 2 without the surrounding
workflow changing.

What already works with no model at all:

* detecting that a card dump has finished and is safe to touch
* grouping frames into bursts and scenes from capture time
* **snapshotting sidecars before anything is written**, which is what creates
  the disagreement dataset the archive cannot supply retroactively
* proposing ratings, and writing them to XMP behind an explicit opt-in
* never processing the same wedding twice

The snapshot step is the reason to adopt this now rather than after the model
exists. Every wedding shot before it is in place is a wedding whose
machine-versus-McKinley disagreements are lost for good.

Safety posture: proposals are always written; sidecars are only modified when
``--write`` is passed, and existing sidecars are backed up first.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .exif import JPEG_EXTS, RAW_EXTS
from .scan import ImageRecord, find_files, scan_wedding
from .snapshot import take_snapshot
from .xmpwrite import write_many

# A frame's proposed fate. Ratings map to how McKinley already works in
# Lightroom rather than inventing a new scale.
RATING_KEEP = 4        # would deliver
RATING_MAYBE = 3       # burst runner-up, worth a look
RATING_DROP = 1        # rejected
RATING_TECHNICAL = 0   # failed a technical gate


@dataclass
class Scored:
    record: ImageRecord
    score: float
    rating: int
    reason: str


# A scorer takes the wedding's records and returns a score per record, higher
# meaning more likely to be delivered. Phase 2 replaces the baseline below with
# a trained head over cached embeddings; the signature does not change.
Scorer = Callable[[list[ImageRecord]], list[float]]


def baseline_scorer(records: list[ImageRecord]) -> list[float]:
    """Deterministic pre-model baseline.

    This is **not** a taste model and must not be mistaken for one. It encodes
    two things that are true regardless of whose wedding it is:

    * frames shot at the extremes of a body's exposure envelope are more often
      technical rejects;
    * within a burst, the later frames are more often the keeper, because the
      photographer is converging on the moment rather than away from it.

    It exists so the workflow can be exercised end to end before the real
    scorer exists. Expect it to be modestly better than random and nothing
    more — the audit's own findings are what justify replacing it.
    """
    scores: list[float] = []
    by_burst: dict[int, list[int]] = {}
    for i, r in enumerate(records):
        if r.burst_id is not None:
            by_burst.setdefault(r.burst_id, []).append(i)

    position: dict[int, float] = {}
    for members in by_burst.values():
        ordered = sorted(
            members,
            key=lambda i: records[i].exif.capture_epoch or 0.0,
        )
        for rank, idx in enumerate(ordered):
            # Later frames in a burst score slightly higher.
            position[idx] = (rank + 1) / len(ordered) if len(ordered) > 1 else 0.5

    for i, r in enumerate(records):
        score = 0.5
        iso = r.exif.iso or 0
        if iso >= 12800:
            score -= 0.15
        shutter = r.exif.shutter
        if shutter and shutter > 1 / 60:
            score -= 0.10          # likely motion blur handheld
        score += 0.25 * (position.get(i, 0.5) - 0.5)
        scores.append(score)
    return scores


def scene_targets(records: list[ImageRecord], overall_keep_rate: float) -> dict[int, int]:
    """How many frames to keep per scene.

    Calibrating per scene rather than globally matters because selectivity is
    not uniform across a wedding day — the audit's ``scene_keep_rate_spread``
    measures exactly this. A single global threshold over-delivers the phases
    you shoot loosely and under-delivers the ones you shoot sparingly.
    """
    counts: dict[int, int] = {}
    for r in records:
        if r.scene_id is not None:
            counts[r.scene_id] = counts.get(r.scene_id, 0) + 1
    return {sid: max(1, round(n * overall_keep_rate)) for sid, n in counts.items()}


def propose(
    records: list[ImageRecord],
    scorer: Scorer = baseline_scorer,
    keep_rate: float = 0.18,
) -> list[Scored]:
    """Rank frames and assign a proposed rating, calibrated per scene."""
    scores = scorer(records)
    targets = scene_targets(records, keep_rate)

    by_scene: dict[int | None, list[int]] = {}
    for i, r in enumerate(records):
        by_scene.setdefault(r.scene_id, []).append(i)

    rating = [RATING_DROP] * len(records)
    reason = ["below scene cut"] * len(records)

    for scene_id, members in by_scene.items():
        target = targets.get(scene_id, max(1, round(len(members) * keep_rate)))
        ranked = sorted(members, key=lambda i: scores[i], reverse=True)
        for rank, idx in enumerate(ranked):
            if rank < target:
                rating[idx] = RATING_KEEP
                reason[idx] = f"top {target} of {len(members)} in scene"
            elif rank < target * 2:
                rating[idx] = RATING_MAYBE
                reason[idx] = "burst runner-up"

    # Technical gates override the ranking — a frame that failed one is not a
    # candidate regardless of where it placed.
    for i, r in enumerate(records):
        if r.exif.capture_epoch is None and r.exif.source == "none":
            rating[i] = RATING_TECHNICAL
            reason[i] = "unreadable metadata"

    return [
        Scored(record=records[i], score=scores[i], rating=rating[i], reason=reason[i])
        for i in range(len(records))
    ]


def _is_stable(root: Path, settle_seconds: float) -> bool:
    """True when nothing under ``root`` has changed recently.

    A card still copying looks exactly like a finished wedding except that its
    files keep changing. Processing mid-copy would produce a cull over a
    partial set, so the automator waits for quiet.
    """
    newest = 0.0
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            count += 1
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                pass
    if count == 0:
        return False
    return (time.time() - newest) >= settle_seconds


def process_wedding(
    raw_root: Path,
    out_dir: Path,
    scorer: Scorer = baseline_scorer,
    keep_rate: float = 0.18,
    write_xmp: bool = False,
    snapshot: bool = True,
    prefer_exiftool: bool = True,
    log=print,
) -> dict:
    """Pre-cull one wedding folder."""
    out_dir.mkdir(parents=True, exist_ok=True)

    records, meta = scan_wedding(
        raw_roots=[raw_root],
        delivered_roots=[],
        prefer_exiftool=prefer_exiftool,
    )
    if not records:
        return {"status": "empty", "raw_root": str(raw_root)}

    scored = propose(records, scorer=scorer, keep_rate=keep_rate)

    # Snapshot BEFORE writing anything. This is the pre-correction state that
    # the disagreement diff is measured against.
    snap = None
    if snapshot:
        snap = take_snapshot([raw_root], out_dir / "snapshots", tag="pre-mckinley")

    decisions = [
        (s.record.path, s.rating, "Green" if s.rating >= RATING_KEEP else None)
        for s in scored
    ]
    write_result = write_many(decisions, backup_root=out_dir, dry_run=not write_xmp)

    proposal = [
        {
            "stem": s.record.stem,
            "path": str(s.record.path),
            "capture_time": s.record.exif.capture_time,
            "scene_id": s.record.scene_id,
            "burst_id": s.record.burst_id,
            "score": round(s.score, 4),
            "proposed_rating": s.rating,
            "reason": s.reason,
        }
        for s in sorted(scored, key=lambda s: (-s.rating, -s.score))
    ]
    (out_dir / "proposal.json").write_text(
        json.dumps(proposal, indent=2, default=str), encoding="utf-8"
    )

    keepers = sum(1 for s in scored if s.rating >= RATING_KEEP)
    result = {
        "status": "ok",
        "raw_root": str(raw_root),
        "frames": len(records),
        "proposed_keepers": keepers,
        "proposed_keep_rate": round(keepers / len(records), 4),
        "scenes": meta["scenes"]["count"],
        "bursts": meta["bursts"]["count"],
        "snapshot": snap["dir"] if snap else None,
        "xmp": write_result,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "cull-result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )

    log(f"    {len(records):,} frames -> {keepers:,} proposed keepers "
        f"({result['proposed_keep_rate']:.1%})")
    if not write_xmp:
        log("    sidecars NOT modified (proposal only; pass --write to apply)")
    return result


def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed": {}}


def run_intake(
    intake: Path,
    out_dir: Path,
    settle_seconds: float = 120.0,
    keep_rate: float = 0.18,
    write_xmp: bool = False,
    prefer_exiftool: bool = True,
    scorer: Scorer = baseline_scorer,
    log=print,
) -> dict:
    """Process every settled, unprocessed wedding folder under ``intake``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "cull-state.json"
    state = _load_state(state_path)

    processed, waiting, skipped, failed = 0, 0, 0, 0

    for child in sorted(intake.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in state["processed"]:
            skipped += 1
            continue
        if not find_files([child], RAW_EXTS | JPEG_EXTS):
            continue
        if not _is_stable(child, settle_seconds):
            waiting += 1
            log(f"  {child.name}: still copying, waiting")
            continue

        log(f"  culling {child.name} ...")
        try:
            result = process_wedding(
                raw_root=child,
                out_dir=out_dir / child.name,
                scorer=scorer,
                keep_rate=keep_rate,
                write_xmp=write_xmp,
                prefer_exiftool=prefer_exiftool,
                log=log,
            )
            state["processed"][child.name] = result
            processed += 1
        except Exception as exc:  # noqa: BLE001 - one bad folder must not stop intake
            failed += 1
            state["processed"][child.name] = {
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=5),
            }
            log(f"    FAILED: {exc}")

        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    log(f"Culled {processed}, waiting {waiting}, already done {skipped}, failed {failed}")
    return {"processed": processed, "waiting": waiting, "skipped": skipped, "failed": failed}


def watch_intake(intake: Path, out_dir: Path, interval: int = 120, log=print, **kwargs) -> None:
    """Poll the intake folder and cull new weddings as they land."""
    log(f"Watching {intake} every {interval}s. Ctrl-C to stop.")
    try:
        while True:
            run_intake(intake, out_dir, log=log, **kwargs)
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Stopped.")
