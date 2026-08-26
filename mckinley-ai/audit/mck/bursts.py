"""Burst and scene segmentation from capture timestamps.

Why this matters more than it looks:

A wedding cull is not mostly "is this photo good". Most frames are shot in runs
of 3-12 near-identical exposures, and the real decision is *which one of this
run survives*. That decision turns on eyes-open, peak expression, hand position
- differences a generic aesthetic model cannot see and a generic "good photo"
score will get wrong.

Segmentation also converts one keep/drop decision into many training signals: a
burst of n frames where 1 was kept yields (n-1) ordered preference pairs. Burst
structure is therefore both the hard part of the problem and the reason the
dataset is larger than the raw image count suggests.

Scene segmentation (the coarser grouping) approximates wedding phases —
getting ready, ceremony, portraits, reception — without any manual labelling,
by cutting the day at long gaps between frames.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_BURST_GAP = 2.0      # seconds between frames within one burst
DEFAULT_SCENE_GAP = 420.0    # 7 minutes of no shooting starts a new scene


@dataclass
class Segment:
    """A contiguous run of frames from one camera body."""

    kind: str            # "burst" | "scene"
    index: int
    body: str
    indices: list[int]   # positions into the caller's record list
    start_epoch: float
    end_epoch: float

    @property
    def size(self) -> int:
        return len(self.indices)

    @property
    def duration(self) -> float:
        return self.end_epoch - self.start_epoch


def _segment_one_body(
    body: str,
    items: list[tuple[int, float]],
    gap: float,
    kind: str,
    start_index: int,
) -> list[Segment]:
    items.sort(key=lambda t: t[1])
    segments: list[Segment] = []
    current: list[tuple[int, float]] = []

    for entry in items:
        if not current:
            current = [entry]
            continue
        if entry[1] - current[-1][1] <= gap:
            current.append(entry)
        else:
            segments.append(_build(kind, start_index + len(segments), body, current))
            current = [entry]
    if current:
        segments.append(_build(kind, start_index + len(segments), body, current))
    return segments


def _build(kind: str, index: int, body: str, current: list[tuple[int, float]]) -> Segment:
    return Segment(
        kind=kind,
        index=index,
        body=body,
        indices=[i for i, _ in current],
        start_epoch=current[0][1],
        end_epoch=current[-1][1],
    )


def segment(
    epochs: list[float | None],
    bodies: list[str],
    gap: float = DEFAULT_BURST_GAP,
    kind: str = "burst",
) -> list[Segment]:
    """Group record indices into time-contiguous segments, per camera body.

    Records without a timestamp cannot be placed and are omitted; the caller
    reports that as timestamp coverage rather than guessing an ordering.
    """
    by_body: dict[str, list[tuple[int, float]]] = {}
    for i, (epoch, body) in enumerate(zip(epochs, bodies)):
        if epoch is None:
            continue
        by_body.setdefault(body, []).append((i, epoch))

    segments: list[Segment] = []
    for body in sorted(by_body):
        segments.extend(_segment_one_body(body, by_body[body], gap, kind, len(segments)))
    return segments


def pair_count(segments: list[Segment], kept: list[bool]) -> int:
    """Ordered preference pairs available from within-burst decisions.

    Only bursts that contain both a kept and a dropped frame teach anything —
    a burst that was kept or dropped wholesale carries no *relative* preference.
    """
    total = 0
    for seg in segments:
        k = sum(1 for i in seg.indices if kept[i])
        d = seg.size - k
        total += k * d
    return total


def summarize(segments: list[Segment], kept: list[bool]) -> dict:
    """Descriptive statistics used by the audit report."""
    if not segments:
        return {
            "count": 0, "singletons": 0, "multi_frame": 0, "largest": 0,
            "mean_size": 0.0, "frames_in_multi": 0,
            "decisive": 0, "wholesale_keep": 0, "wholesale_drop": 0,
            "preference_pairs": 0,
        }

    sizes = [s.size for s in segments]
    multi = [s for s in segments if s.size > 1]
    decisive = wholesale_keep = wholesale_drop = 0
    for seg in multi:
        k = sum(1 for i in seg.indices if kept[i])
        if k == 0:
            wholesale_drop += 1
        elif k == seg.size:
            wholesale_keep += 1
        else:
            decisive += 1

    return {
        "count": len(segments),
        "singletons": sum(1 for s in sizes if s == 1),
        "multi_frame": len(multi),
        "largest": max(sizes),
        "mean_size": round(sum(sizes) / len(sizes), 2),
        "frames_in_multi": sum(s.size for s in multi),
        "decisive": decisive,
        "wholesale_keep": wholesale_keep,
        "wholesale_drop": wholesale_drop,
        "preference_pairs": pair_count(segments, kept),
    }
