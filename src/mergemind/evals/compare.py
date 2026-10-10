"""v1 vs v2 across every backtest, with paired bootstrap 95% CIs on the difference
(the same issues / PRs are scored by both versions, so each resample keeps the pairs)."""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

from mergemind.evals import prcheck
from mergemind.evals.dataset import Item


def paired_ci(xs: list[float], ys: list[float], n: int = 2000, seed: int = 7) -> dict:
    diffs = [y - x for x, y in zip(xs, ys)]
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return {"n": len(diffs), "v1": statistics.fmean(xs), "v2": statistics.fmean(ys), "diff": statistics.fmean(diffs),
            "ci": (boots[int(0.025 * n)], boots[int(0.975 * n) - 1])}


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def pr_pairs(eval_dirs: list[Path], items: list[Item], base: str, run: str) -> dict[str, dict]:
    """Hold rate and any-out-of-scope rate on the test split, paired by PR."""
    out: dict[str, dict] = {}
    rows_a, rows_b = [], []
    for d in eval_dirs:
        a = {r["pr"]: r for r in _load(d / f"pr-static-{base}.jsonl") if not r["error"]}
        b = {r["pr"]: r for r in _load(d / f"pr-static-{run}.jsonl") if not r["error"]}
        for pr in sorted(a.keys() & b.keys()):
            rows_a.append(prcheck.PRRow(**a[pr]))
            rows_b.append(prcheck.PRRow(**b[pr]))
    _, test_a = prcheck.split_rows(rows_a, items)
    _, test_b = prcheck.split_rows(rows_b, items)
    out["Safety-scan hold rate on merged fix PRs (lower is better)"] = paired_ci(
        [float(bool(r.flags)) for r in test_a], [float(bool(r.flags)) for r in test_b])
    both = [(x, y) for x, y in zip(test_a, test_b) if x.out_of_scope is not None and y.out_of_scope is not None]
    out["Fix PRs touching a source file outside the scope (lower is better)"] = paired_ci(
        [float(bool(x.out_of_scope)) for x, _ in both], [float(bool(y.out_of_scope)) for _, y in both])
    return out


def triage_pairs(eval_dirs: list[Path], base: str, run: str) -> tuple[dict[str, dict], dict]:
    a, b = {}, {}
    for d in eval_dirs:
        a.update({(r["repo"], r["number"]): r for r in _load(d / f"triage-{base}.jsonl") if not r["error"]})
        b.update({(r["repo"], r["number"]): r for r in _load(d / f"triage-{run}.jsonl") if not r["error"]})
    keys = sorted(a.keys() & b.keys())
    hit = lambda r, k: float(bool(set(r["localized"][:k]) & set(r["truth"])))  # noqa: E731
    metrics = {
        "Accepted (all sample issues were real, fixed bugs)": lambda r: float(r["disposition"] == "accept"),
        "Investigator localization Hit@1": lambda r: hit(r, 1),
        "Investigator localization Hit@3": lambda r: hit(r, 3),
        "Verifier rejected the final report (lower is better)": lambda r: float(r["verifier_ok"] is False),
    }
    out = {name: paired_ci([fn(a[k]) for k in keys], [fn(b[k]) for k in keys]) for name, fn in metrics.items()}
    fresh = [k for k in keys if a[k]["in_tok"] and b[k]["in_tok"]]
    cost = {
        "n": len(keys),
        "dispositions_v1": _count(a[k]["disposition"] for k in keys),
        "dispositions_v2": _count(b[k]["disposition"] for k in keys),
        "tokens_median_v1": statistics.median(a[k]["in_tok"] + a[k]["out_tok"] for k in fresh) if fresh else None,
        "tokens_median_v2": statistics.median(b[k]["in_tok"] + b[k]["out_tok"] for k in fresh) if fresh else None,
        "seconds_median_v1": statistics.median(a[k]["seconds"] for k in keys) if keys else None,
        "seconds_median_v2": statistics.median(b[k]["seconds"] for k in keys) if keys else None,
        "cached_calls_v2": sum(b[k]["cached"] for k in keys),
        "changed": [{"issue": f"{k[0]}#{k[1]}", "v1": a[k]["disposition"], "v2": b[k]["disposition"],
                     "v2_notes": b[k].get("notes", [])} for k in keys if a[k]["disposition"] != b[k]["disposition"]],
    }
    return out, cost


def _count(xs) -> dict:
    d: dict[str, int] = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def row(name: str, d: dict, fmt: str = "{:.2f}") -> str:
    lo, hi = d["ci"]
    star = " *" if lo > 0 or hi < 0 else ""
    return (f"| {name} | {d['n']} | {fmt.format(d['v1'])} | {fmt.format(d['v2'])} | "
            f"{d['diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]{star} |")
