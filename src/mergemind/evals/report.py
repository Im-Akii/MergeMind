"""Markdown + JSON reports for the backtests (evidence/eval/)."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from typing import Callable

from mergemind.evals.backtest import Row
from mergemind.evals.metrics import hit_at, mrr, split, summarize, test_recall

ORDER = ["fused", "entire", "lexical", "mentioned", "recency"]
LABEL = {
    "fused": "MergeMind v2 (entire-graph + lexical, fused)",
    "entire": "MergeMind (entire-graph)",
    "lexical": "MergeMind, lexical backend (ablation)",
    "mentioned": "Baseline: files named in the issue",
    "recency": "Baseline: most-changed files (180 d)",
}
COLUMNS = ["Hit@1", "Hit@3", "Hit@5", "File-Recall@5", "MRR", "Scope coverage", "Test recall"]


def _cell(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, tuple):
        mean, lo, hi = v
        if math.isnan(mean):
            return "–"
        return f"{mean:.2f} [{lo:.2f}–{hi:.2f}]"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def table(summary: dict, title: str) -> str:
    lines = [f"### {title}", "", "| Method | n | " + " | ".join(COLUMNS) + " | s/issue |",
             "|---|---|" + "---|" * len(COLUMNS) + "---|"]
    for m in ORDER:
        if m not in summary:
            continue
        s = summary[m]
        lines.append(f"| {LABEL[m]} | {s['n']} | " + " | ".join(_cell(s.get(c)) for c in COLUMNS)
                     + f" | {_cell(s.get('Seconds/issue (median)'))} |")
    return "\n".join(lines)


def locate_report(rows: list[Row], run: str, repos: list[str]) -> tuple[str, dict]:
    usable = [r for r in rows if not r.error and r.truth]
    errors = Counter(r.error for r in rows if r.error)
    dev, test = split(usable)
    methods = [m for m in ORDER if any(m in r.ranked for r in usable)]
    s_all, s_dev, s_test = summarize(usable, methods), summarize(dev, methods), summarize(test, methods)
    per_repo = {repo: summarize([r for r in usable if r.repo == repo], methods) for repo in repos}

    md = [f"# Localization backtest `{run}`", "",
          f"Repos: {', '.join(repos)}. Issues: {len(rows)} total, {len(usable)} usable "
          f"({dict(errors) or 'no exclusions'}).",
          "Leakage control: code checked out at the last default-branch commit before the start of each issue's "
          "month; ground truth = source files modified by the merged PR that closed the issue. "
          "No LLM is involved in this table. Cells: mean [bootstrap 95% CI].", "",
          table(s_test, f"Test split (newest 40% per repo, n={len(test)}) — headline"), "",
          table(s_dev, f"Dev split (oldest 60%, n={len(dev)})"), "",
          table(s_all, f"All usable issues (n={len(usable)})"), ""]
    for repo, s in per_repo.items():
        md += [table(s, f"{repo}"), ""]
    md += ["Definitions: Hit@k = a fix file is among the top-k predicted files; File-Recall@k = share of fix files "
           "in the top k; MRR = 1/rank of the first fix file; Scope coverage = share of fix files inside the scope "
           "MergeMind would allow a contributor to touch; Test recall = share of the fix PR's changed test files "
           "that MergeMind's impact analysis named."]
    data = {"run": run, "repos": repos, "n_total": len(rows), "n_usable": len(usable), "exclusions": dict(errors),
            "test": s_test, "dev": s_dev, "all": s_all, "per_repo": per_repo}
    return "\n".join(md), data


PAIRED: dict[str, Callable[[Row, str], float | None]] = {
    "Hit@1": lambda r, m: hit_at(r, m, 1),
    "Hit@3": lambda r, m: hit_at(r, m, 3),
    "Hit@5": lambda r, m: hit_at(r, m, 5),
    "MRR": mrr,
    "Test recall": test_recall,
}


def paired(rows: list[Row], a: str, b: str, n: int = 2000, seed: int = 7) -> dict[str, dict]:
    """Per metric: mean of `a`, mean of `b`, and the paired bootstrap 95% CI of b - a
    (same issues resampled for both, so the CI of the difference is tighter than two separate CIs)."""
    rng = random.Random(seed)
    out = {}
    for name, fn in PAIRED.items():
        pairs = [(fn(r, a), fn(r, b)) for r in rows if a in r.ranked and b in r.ranked]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        if not pairs:
            continue
        diffs = [y - x for x, y in pairs]
        boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
        out[name] = {"n": len(pairs), a: statistics.fmean(x for x, _ in pairs), b: statistics.fmean(y for _, y in pairs),
                     "diff": statistics.fmean(diffs), "ci": (boots[int(0.025 * n)], boots[int(0.975 * n) - 1])}
    return out


def paired_table(res: dict, a: str, b: str, title: str) -> str:
    lines = [f"### {title}", "", f"| Metric | n | v1 ({a}) | v2 ({b}) | v2 - v1 [95% CI] |", "|---|---|---|---|---|"]
    for name, d in res.items():
        lo, hi = d["ci"]
        sig = " *" if lo > 0 or hi < 0 else ""
        lines.append(f"| {name} | {d['n']} | {d[a]:.3f} | {d[b]:.3f} | {d['diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]{sig} |")
    lines += ["", "`*` = the 95% CI of the difference excludes 0."]
    return "\n".join(lines)
