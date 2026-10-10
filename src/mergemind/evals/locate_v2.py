"""Localization v2, computed offline from the v1 backtest rows (no re-run of the graph).

v1 stored, per issue, each backend's ranked files, impact tests and scope at the leakage-free
snapshot. v2 changes only how those are combined, so it can be recomputed exactly:

  fused   weighted RRF of the entire-graph and lexical rankings (code/localize.fuse_files)
  tests   union of entire-graph callers' tests and lexical test mentions
  scope   union of both scopes, widened with top ranked files and co-changed files
          (code/scope.widen; co-change history read at the snapshot commit, so no leakage)

Weights and scope parameters are chosen on the dev split (oldest 60% per repo) only.
"""

from __future__ import annotations

import itertools
import json
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

from mergemind.code.graph import co_changed, is_doc, is_test
from mergemind.code.localize import fuse_files
from mergemind.code.repo_cache import git
from mergemind.code.scope import ScopeParams, out_of_scope, widen
from mergemind.evals.backtest import Row
from mergemind.evals.dataset import Item
from mergemind.evals.metrics import bootstrap, mrr, split

log = logging.getLogger(__name__)

FUSE_GRID = {"k": (10, 30, 60), "w_entire": (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)}
SCOPE_GRID = {"base": ("entire", "union"), "top_ranked": (0, 3, 5), "co_change_files": (0, 1, 2, 3),
              "co_change_top": (2, 3, 5), "co_change_min": (2, 3)}
# Scope choice on dev: walk the size/coverage frontier and stop when one more source file in the
# median scope buys less than 2 points of all-in-scope rate. (The first rule tried, "median size
# <= 1.5x v1's", allowed only 3 files because v1's median is 2, so it could not widen at all.)
MIN_GAIN_PER_FILE = 0.02
SCOPE_BUDGET = 1.5  # the abandoned rule, still reported for transparency


class History:
    """Per-snapshot file lists and co-change counts, memoized (git only, read at the snapshot)."""

    def __init__(self, repo_path: Path):
        self.repo = repo_path
        self._present: dict[str, set[str]] = {}
        self._co: dict[tuple[str, str], list[tuple[str, int]]] = {}

    def present(self, sha: str) -> set[str]:
        if sha not in self._present:
            self._present[sha] = set(git(self.repo, "ls-tree", "-r", "--name-only", sha, check=False).splitlines())
        return self._present[sha]

    def co(self, sha: str, files: list[str]) -> dict[str, list[tuple[str, int]]]:
        out = {}
        for f in files:
            if (sha, f) not in self._co:
                self._co[(sha, f)] = co_changed(self.repo, f, top=20, rev=sha)
            out[f] = self._co[(sha, f)]
        return out


_HISTORIES: dict[Path, History] = {}


def history(repo_path: Path) -> History:
    return _HISTORIES.setdefault(repo_path, History(repo_path))


def fused_ranking(r: Row, k: int, w_entire: float) -> list[str]:
    lists = {m: r.ranked[m] for m in ("entire", "lexical") if r.ranked.get(m)}
    return fuse_files(lists, {"entire": w_entire, "lexical": 1.0}, k)


def pr_source(item: Item) -> list[str]:
    return [f for f in item.fix_files if not is_test(f) and not is_doc(f)]


def scope_for(r: Row, ranked: list[str], hist: History, p: dict) -> list[str]:
    base = r.scope.get("entire") or r.scope.get("lexical") or []
    if p["base"] == "union":
        base = list(dict.fromkeys(base + (r.scope.get("lexical") or [])))
    params = ScopeParams(p["top_ranked"], p["co_change_files"], p["co_change_top"], p["co_change_min"])
    present = hist.present(r.snapshot)
    return widen(base, ranked, hist.repo, present, params, co=hist.co(r.snapshot, ranked[: p["co_change_files"]]))


def scope_stats(rows: list[Row], items: dict[tuple[str, int], Item], hists: dict[str, History],
                scopes: list[list[str]], allow_new: bool = True) -> dict:
    in_all, sizes, cov = [], [], []
    for r, sc in zip(rows, scopes):
        it = items[(r.repo, r.number)]
        present = hists[r.repo].present(r.snapshot)
        changed = [(f, f not in present) for f in pr_source(it)]
        in_all.append(float(not out_of_scope(changed, sc, allow_new)))
        sizes.append(sum(1 for f in sc if not is_test(f) and not is_doc(f)))
        cov.append(len(set(sc) & set(r.truth)) / len(r.truth))
    return {"all_in_scope": statistics.fmean(in_all), "median_source_files": statistics.median(sizes),
            "coverage": statistics.fmean(cov), "_in_all": in_all, "_sizes": sizes}


def tune(dev: list[Row], items, hists) -> tuple[dict, dict, list[dict], list[dict]]:
    fuse_rows = []
    for k, w in itertools.product(FUSE_GRID["k"], FUSE_GRID["w_entire"]):
        ranked = {id(r): fused_ranking(r, k, w) for r in dev}
        probe = [replace(r, ranked={"f": ranked[id(r)]}) for r in dev]
        fuse_rows.append({"k": k, "w_entire": w, "MRR": statistics.fmean(mrr(r, "f") for r in probe),
                          "Hit@5": statistics.fmean(float(bool(set(r.ranked["f"][:5]) & set(r.truth))) for r in probe)})
    best_fuse = max(fuse_rows, key=lambda x: (round(x["MRR"], 4), x["Hit@5"], x["w_entire"] == 1.0))
    ranked = {id(r): fused_ranking(r, best_fuse["k"], best_fuse["w_entire"]) for r in dev}

    v1 = scope_stats(dev, items, hists, [r.scope.get("entire") or r.scope.get("lexical") or [] for r in dev])
    budget = SCOPE_BUDGET * v1["median_source_files"]
    scope_rows = []
    keys = list(SCOPE_GRID)
    for combo in itertools.product(*SCOPE_GRID.values()):
        p = dict(zip(keys, combo))
        if p["co_change_files"] == 0 and (p["co_change_top"], p["co_change_min"]) != (2, 2):
            continue  # identical to the (2, 2) variant
        s = scope_stats(dev, items, hists, [scope_for(r, ranked[id(r)], hists[r.repo], p) for r in dev])
        scope_rows.append({**p, "all_in_scope": s["all_in_scope"], "median_source_files": s["median_source_files"],
                           "coverage": s["coverage"]})
    # frontier: the best variant at each median size; walk up while each extra file still pays
    frontier: dict[float, dict] = {}
    for s in scope_rows:
        m = s["median_source_files"]
        if m not in frontier or s["all_in_scope"] > frontier[m]["all_in_scope"]:
            frontier[m] = s
    sizes = sorted(frontier)
    best_scope = frontier[sizes[0]]
    for m in sizes[1:]:
        cand = frontier[m]
        gain = (cand["all_in_scope"] - best_scope["all_in_scope"]) / (m - best_scope["median_source_files"])
        if gain < MIN_GAIN_PER_FILE:
            break
        best_scope = cand
    best_scope = {**best_scope, "v1_all_in_scope": v1["all_in_scope"],
                  "v1_median_source_files": v1["median_source_files"], "budget": budget,
                  "frontier": [{"median_source_files": m, "all_in_scope": frontier[m]["all_in_scope"]} for m in sizes]}
    return best_fuse, best_scope, fuse_rows, scope_rows


def build(rows: list[Row], items: dict[tuple[str, int], Item], repo_paths: dict[str, Path]) -> tuple[list[Row], dict]:
    usable = [r for r in rows if not r.error and r.truth]
    hists = {repo: history(p) for repo, p in repo_paths.items()}
    # warm git lookups in parallel (one thread per repo keeps the memo dicts single-writer)
    def warm(repo: str) -> None:
        h = hists[repo]
        for r in usable:
            if r.repo == repo:
                h.present(r.snapshot)
                cands = list(dict.fromkeys((r.ranked.get("entire") or [])[:3] + (r.ranked.get("lexical") or [])[:3]))
                h.co(r.snapshot, cands)
    with ThreadPoolExecutor(max_workers=len(hists)) as pool:
        list(pool.map(warm, hists))

    dev, test = split(usable)
    best_fuse, best_scope, fuse_rows, scope_rows = tune(dev, items, hists)
    log.info("fusion %s  scope %s", best_fuse, best_scope)

    out: list[Row] = []
    for r in rows:
        if r.error or not r.truth:
            out.append(r)
            continue
        ranked = fused_ranking(r, best_fuse["k"], best_fuse["w_entire"])
        n = replace(r, ranked={**r.ranked, "fused": ranked}, scope={**r.scope}, tests={**r.tests},
                    seconds={**r.seconds}, confidence={**r.confidence})
        n.tests["fused"] = sorted(set(r.tests.get("entire", [])) | set(r.tests.get("lexical", [])))
        n.scope["fused"] = scope_for(r, ranked, hists[r.repo], best_scope)
        n.seconds["fused"] = round(r.seconds.get("entire", 0) + r.seconds.get("lexical", 0), 2)
        out.append(n)
    return out, {"fusion": best_fuse, "scope": best_scope, "fusion_grid": fuse_rows, "scope_grid": scope_rows}


def scope_summary(rows: list[Row], items, repo_paths: dict[str, Path]) -> dict:
    """All-in-scope rate and scope size, v1 (entire) vs v2 (fused), with bootstrap CIs."""
    hists = {repo: history(p) for repo, p in repo_paths.items()}
    usable = [r for r in rows if not r.error and r.truth and "fused" in r.scope]
    out = {}
    for name, part in zip(("dev", "test"), split(usable)):
        res = {}
        for label, key in (("v1", "entire"), ("v2", "fused")):
            s = scope_stats(part, items, hists, [r.scope.get(key) or r.scope.get("lexical") or [] for r in part],
                            allow_new=label == "v2")
            res[label] = {"all_in_scope": bootstrap(s["_in_all"]), "median_source_files": s["median_source_files"],
                          "coverage": s["coverage"], "n": len(part)}
        out[name] = res
    return out


def save(rows: list[Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(asdict(r)) for r in rows) + "\n", encoding="utf-8")
