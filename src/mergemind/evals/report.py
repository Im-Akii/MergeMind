"""Markdown + JSON reports for the backtests (evidence/eval/)."""

from __future__ import annotations

import math
from collections import Counter

from mergemind.evals.backtest import Row
from mergemind.evals.metrics import split, summarize

ORDER = ["entire", "lexical", "mentioned", "recency"]
LABEL = {
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
