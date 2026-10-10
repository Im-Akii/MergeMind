"""Backtest metrics with bootstrap 95% confidence intervals and a time-based dev/test split."""

from __future__ import annotations

import random
import statistics
from typing import Callable, Sequence

from mergemind.evals.backtest import Row

KS = (1, 3, 5)


def split(rows: list[Row], dev_fraction: float = 0.6) -> tuple[list[Row], list[Row]]:
    """Per repo, the oldest 60% is dev and the newest 40% is test."""
    dev, test = [], []
    by_repo: dict[str, list[Row]] = {}
    for r in rows:
        by_repo.setdefault(r.repo, []).append(r)
    for rs in by_repo.values():
        rs = sorted(rs, key=lambda r: r.created_at)
        cut = int(len(rs) * dev_fraction)
        dev += rs[:cut]
        test += rs[cut:]
    return dev, test


def hit_at(r: Row, m: str, k: int) -> float:
    return float(bool(set(r.ranked.get(m, [])[:k]) & set(r.truth)))


def recall_at(r: Row, m: str, k: int) -> float:
    return len(set(r.ranked.get(m, [])[:k]) & set(r.truth)) / len(r.truth)


def mrr(r: Row, m: str) -> float:
    for i, f in enumerate(r.ranked.get(m, []), 1):
        if f in r.truth:
            return 1.0 / i
    return 0.0


def scope_coverage(r: Row, m: str) -> float | None:
    if m not in r.scope:
        return None
    return len(set(r.scope[m]) & set(r.truth)) / len(r.truth)


def test_recall(r: Row, m: str) -> float | None:
    if m not in r.tests or not r.truth_tests:
        return None
    return len(set(r.tests[m]) & set(r.truth_tests)) / len(r.truth_tests)


def bootstrap(values: Sequence[float], n: int = 2000, seed: int = 7) -> tuple[float, float, float]:
    if not values:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return statistics.fmean(values), means[int(0.025 * n)], means[int(0.975 * n) - 1]


def summarize(rows: list[Row], methods: Sequence[str]) -> dict[str, dict[str, tuple[float, float, float]]]:
    usable = [r for r in rows if not r.error and r.truth]
    out: dict[str, dict] = {}
    for m in methods:
        rs = [r for r in usable if m in r.ranked]
        if not rs:
            continue
        metrics: dict[str, Callable[[Row], float | None]] = {
            **{f"Hit@{k}": (lambda r, k=k: hit_at(r, m, k)) for k in KS},
            **{f"File-Recall@{k}": (lambda r, k=k: recall_at(r, m, k)) for k in KS},
            "MRR": lambda r: mrr(r, m),
            "Scope coverage": lambda r: scope_coverage(r, m),
            "Test recall": lambda r: test_recall(r, m),
        }
        out[m] = {"n": len(rs)}
        for name, fn in metrics.items():
            vals = [v for v in (fn(r) for r in rs) if v is not None]
            if vals:
                out[m][name] = bootstrap(vals)
        secs = [r.seconds.get(m) for r in rs if r.seconds.get(m) is not None]
        if secs:
            out[m]["Seconds/issue (median)"] = statistics.median(secs)
    return out
