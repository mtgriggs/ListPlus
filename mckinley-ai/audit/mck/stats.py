"""Small statistics helpers (stdlib only, no numpy dependency)."""

from __future__ import annotations

import math
from collections import Counter


def average_ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged — the basis of the Mann-Whitney AUC."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def auc(scores: list[float], labels: list[bool]) -> float | None:
    """Probability a random positive outranks a random negative.

    0.5 means the score carries no information about the label; 1.0 means it
    separates them perfectly. Returns None when one class is absent.
    """
    pairs = [(s, y) for s, y in zip(scores, labels) if s is not None]
    if not pairs:
        return None
    s = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = average_ranks(s)
    rank_sum = sum(r for r, yi in zip(ranks, y) if yi)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def entropy(counts: list[int]) -> float:
    """Shannon entropy in bits — used to detect a single-preset archive."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h


def describe(values: list[float]) -> dict:
    """Count / mean / sd / min / median / max for a numeric column."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    vals_sorted = sorted(vals)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n if n > 1 else 0.0
    mid = n // 2
    median = vals_sorted[mid] if n % 2 else (vals_sorted[mid - 1] + vals_sorted[mid]) / 2
    return {
        "n": n,
        "mean": round(mean, 4),
        "sd": round(math.sqrt(var), 4),
        "min": round(vals_sorted[0], 4),
        "median": round(median, 4),
        "max": round(vals_sorted[-1], 4),
    }


def contingency(keys: list, labels: list[bool]) -> dict:
    """Keep-rate broken down by a categorical column."""
    total = Counter()
    kept = Counter()
    for k, y in zip(keys, labels):
        key = "(none)" if k is None else str(k)
        total[key] += 1
        if y:
            kept[key] += 1
    return {
        k: {
            "n": total[k],
            "kept": kept[k],
            "keep_rate": round(kept[k] / total[k], 4) if total[k] else 0.0,
        }
        for k in sorted(total, key=lambda x: (-total[x], x))
    }


def cluster_timestamps(times: list[float], gap: float = 300.0) -> list[tuple[float, int]]:
    """Group modification times into write sessions.

    All sidecars sharing one write session means the archive holds a single
    final state — no before/after, so no recoverable disagreement history.
    """
    vals = sorted(t for t in times if t is not None)
    if not vals:
        return []
    clusters: list[tuple[float, int]] = []
    start = vals[0]
    count = 1
    for prev, cur in zip(vals, vals[1:]):
        if cur - prev <= gap:
            count += 1
        else:
            clusters.append((start, count))
            start = cur
            count = 1
    clusters.append((start, count))
    return clusters
