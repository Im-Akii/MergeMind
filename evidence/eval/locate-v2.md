# Localization `v2` vs `v1` (offline, from the v1 backtest rows)

v2 = weighted RRF of the entire-graph and lexical rankings + test union + widened scope. Nothing was re-run: the v1 rows hold each backend's ranking, tests and scope at the leakage-free snapshot, and co-change history is read at the same snapshot commit.

Tuned on the dev split only (n=246): fusion k=10, entire weight 0.75 (dev MRR 0.641); scope base=entire, top_ranked=5, co-changed: top 2 files (seen together >= 2x) around the top 2 ranked files. Scope rule (dev only): widen while each extra source file in the median scope adds >= 2 points of all-in-scope rate. The first rule tried (median size <= 1.5x v1's = 3 files) could not widen at all because v1's median is 2 files; it was replaced before looking at the test split. Dev frontier: 2 files -> 0.47, 4 files -> 0.57, 5 files -> 0.62, 6 files -> 0.65, 7 files -> 0.67, 8 files -> 0.69, 9 files -> 0.70, 10 files -> 0.70.

### Test split, v1 vs v2 (n=166) — headline

| Metric | n | v1 (entire) | v2 (fused) | v2 - v1 [95% CI] |
|---|---|---|---|---|
| Hit@1 | 166 | 0.452 | 0.476 | +0.024 [-0.030, +0.084] |
| Hit@3 | 166 | 0.675 | 0.741 | +0.066 [+0.012, +0.127] * |
| Hit@5 | 166 | 0.765 | 0.813 | +0.048 [-0.006, +0.102] |
| MRR | 166 | 0.577 | 0.623 | +0.046 [+0.008, +0.082] * |
| Test recall | 132 | 0.497 | 0.740 | +0.242 [+0.176, +0.314] * |

`*` = the 95% CI of the difference excludes 0.

### Scope: fix PRs whose source changes all fall inside the allowed scope

| Split | v1 all-in-scope | v2 all-in-scope | v1 median size | v2 median size |
|---|---|---|---|---|
| test (n=166) | 0.52 [0.44–0.59] | 0.70 [0.63–0.77] | 3 | 7 |
| dev (n=246) | 0.46 [0.40–0.53] | 0.67 [0.61–0.73] | 2 | 7 |

Size = source files in scope (tests and docs are always allowed). v2 also allows new files created next to scoped source files.

### Dev split (n=246, used for tuning)

| Metric | n | v1 (entire) | v2 (fused) | v2 - v1 [95% CI] |
|---|---|---|---|---|
| Hit@1 | 246 | 0.419 | 0.541 | +0.122 [+0.069, +0.175] * |
| Hit@3 | 246 | 0.650 | 0.703 | +0.053 [+0.016, +0.093] * |
| Hit@5 | 246 | 0.760 | 0.776 | +0.016 [-0.033, +0.065] |
| MRR | 246 | 0.555 | 0.641 | +0.086 [+0.052, +0.121] * |
| Test recall | 194 | 0.406 | 0.662 | +0.256 [+0.201, +0.315] * |

`*` = the 95% CI of the difference excludes 0.

---
# Localization backtest `v2`

Repos: encode/httpx, pydantic/pydantic, pytest-dev/pytest. Issues: 450 total, 412 usable ({'fix files did not exist at the snapshot': 38}).
Leakage control: code checked out at the last default-branch commit before the start of each issue's month; ground truth = source files modified by the merged PR that closed the issue. No LLM is involved in this table. Cells: mean [bootstrap 95% CI].

### Test split (newest 40% per repo, n=166) — headline

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 166 | 0.48 [0.40–0.55] | 0.74 [0.67–0.81] | 0.81 [0.75–0.87] | 0.71 [0.65–0.77] | 0.62 [0.56–0.68] | 0.80 [0.74–0.85] | 0.74 [0.67–0.81] | 110.3 |
| MergeMind (entire-graph) | 166 | 0.45 [0.37–0.52] | 0.67 [0.60–0.74] | 0.77 [0.70–0.83] | 0.68 [0.61–0.74] | 0.58 [0.52–0.64] | 0.59 [0.52–0.66] | 0.50 [0.41–0.58] | 106.2 |
| MergeMind, lexical backend (ablation) | 166 | 0.45 [0.37–0.52] | 0.73 [0.66–0.80] | 0.80 [0.73–0.86] | 0.69 [0.62–0.75] | 0.59 [0.53–0.65] | 0.63 [0.57–0.70] | 0.62 [0.55–0.70] | 4.8 |
| Baseline: files named in the issue | 166 | 0.13 [0.08–0.19] | 0.14 [0.09–0.20] | 0.15 [0.10–0.20] | 0.14 [0.09–0.19] | 0.14 [0.09–0.19] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 166 | 0.17 [0.11–0.23] | 0.35 [0.28–0.43] | 0.44 [0.37–0.52] | 0.33 [0.27–0.40] | 0.28 [0.22–0.33] | – | – | 0.3 |

### Dev split (oldest 60%, n=246)

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 246 | 0.54 [0.48–0.61] | 0.70 [0.65–0.76] | 0.78 [0.72–0.83] | 0.68 [0.62–0.73] | 0.64 [0.59–0.69] | 0.76 [0.71–0.80] | 0.66 [0.60–0.72] | 88.7 |
| MergeMind (entire-graph) | 246 | 0.42 [0.36–0.48] | 0.65 [0.59–0.71] | 0.76 [0.71–0.81] | 0.65 [0.59–0.70] | 0.55 [0.50–0.60] | 0.55 [0.49–0.61] | 0.41 [0.34–0.47] | 85.3 |
| MergeMind, lexical backend (ablation) | 246 | 0.48 [0.41–0.54] | 0.67 [0.61–0.72] | 0.72 [0.67–0.78] | 0.62 [0.56–0.67] | 0.58 [0.53–0.63] | 0.66 [0.61–0.72] | 0.60 [0.53–0.66] | 4.2 |
| Baseline: files named in the issue | 246 | 0.11 [0.07–0.15] | 0.12 [0.08–0.16] | 0.12 [0.08–0.16] | 0.10 [0.07–0.14] | 0.11 [0.07–0.15] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 246 | 0.17 [0.12–0.22] | 0.33 [0.28–0.40] | 0.42 [0.37–0.49] | 0.32 [0.27–0.37] | 0.28 [0.24–0.33] | – | – | 0.3 |

### All usable issues (n=412)

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 412 | 0.51 [0.47–0.56] | 0.72 [0.67–0.76] | 0.79 [0.75–0.83] | 0.69 [0.65–0.73] | 0.63 [0.60–0.67] | 0.77 [0.74–0.81] | 0.69 [0.65–0.74] | 92.8 |
| MergeMind (entire-graph) | 412 | 0.43 [0.38–0.48] | 0.66 [0.61–0.70] | 0.76 [0.72–0.80] | 0.66 [0.62–0.70] | 0.56 [0.52–0.60] | 0.57 [0.52–0.61] | 0.44 [0.39–0.49] | 88.6 |
| MergeMind, lexical backend (ablation) | 412 | 0.47 [0.42–0.51] | 0.69 [0.65–0.73] | 0.75 [0.71–0.79] | 0.65 [0.60–0.68] | 0.58 [0.55–0.62] | 0.65 [0.61–0.69] | 0.61 [0.56–0.66] | 4.5 |
| Baseline: files named in the issue | 412 | 0.12 [0.09–0.15] | 0.13 [0.10–0.16] | 0.13 [0.10–0.17] | 0.12 [0.09–0.15] | 0.12 [0.09–0.15] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 412 | 0.17 [0.13–0.20] | 0.34 [0.30–0.39] | 0.43 [0.39–0.48] | 0.32 [0.28–0.36] | 0.28 [0.25–0.32] | – | – | 0.3 |

### encode/httpx

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 128 | 0.59 [0.51–0.68] | 0.84 [0.77–0.90] | 0.87 [0.80–0.92] | 0.78 [0.71–0.84] | 0.71 [0.65–0.78] | 0.85 [0.80–0.90] | 0.77 [0.69–0.85] | 24.8 |
| MergeMind (entire-graph) | 128 | 0.52 [0.43–0.60] | 0.74 [0.66–0.82] | 0.85 [0.79–0.91] | 0.76 [0.70–0.83] | 0.64 [0.58–0.71] | 0.64 [0.56–0.71] | 0.39 [0.30–0.49] | 20.5 |
| MergeMind, lexical backend (ablation) | 128 | 0.57 [0.48–0.66] | 0.82 [0.75–0.88] | 0.84 [0.78–0.91] | 0.73 [0.66–0.79] | 0.69 [0.62–0.76] | 0.72 [0.65–0.79] | 0.74 [0.65–0.82] | 4.3 |
| Baseline: files named in the issue | 128 | 0.09 [0.05–0.15] | 0.12 [0.06–0.18] | 0.12 [0.07–0.19] | 0.11 [0.06–0.16] | 0.11 [0.06–0.16] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 128 | 0.30 [0.22–0.38] | 0.52 [0.43–0.60] | 0.64 [0.55–0.73] | 0.51 [0.44–0.59] | 0.44 [0.38–0.51] | – | – | 0.4 |

### pydantic/pydantic

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 135 | 0.41 [0.33–0.50] | 0.59 [0.51–0.67] | 0.70 [0.61–0.77] | 0.59 [0.51–0.67] | 0.54 [0.47–0.61] | 0.68 [0.61–0.75] | 0.54 [0.45–0.64] | 145.2 |
| MergeMind (entire-graph) | 135 | 0.38 [0.30–0.46] | 0.59 [0.50–0.67] | 0.69 [0.61–0.77] | 0.57 [0.50–0.65] | 0.50 [0.44–0.58] | 0.52 [0.45–0.60] | 0.42 [0.33–0.52] | 136.5 |
| MergeMind, lexical backend (ablation) | 135 | 0.36 [0.27–0.44] | 0.51 [0.43–0.59] | 0.63 [0.55–0.70] | 0.52 [0.44–0.60] | 0.46 [0.39–0.53] | 0.51 [0.44–0.59] | 0.42 [0.33–0.51] | 8.8 |
| Baseline: files named in the issue | 135 | 0.19 [0.12–0.25] | 0.19 [0.13–0.26] | 0.19 [0.13–0.26] | 0.17 [0.11–0.23] | 0.19 [0.12–0.26] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 135 | 0.10 [0.05–0.16] | 0.30 [0.22–0.38] | 0.35 [0.27–0.43] | 0.23 [0.17–0.30] | 0.21 [0.16–0.26] | – | – | 0.4 |

### pytest-dev/pytest

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind v2 (entire-graph + lexical, fused) | 149 | 0.54 [0.46–0.62] | 0.73 [0.66–0.81] | 0.81 [0.74–0.87] | 0.71 [0.64–0.77] | 0.65 [0.59–0.72] | 0.79 [0.74–0.85] | 0.77 [0.70–0.83] | 93.8 |
| MergeMind (entire-graph) | 149 | 0.41 [0.33–0.49] | 0.66 [0.58–0.73] | 0.75 [0.68–0.82] | 0.65 [0.58–0.72] | 0.55 [0.48–0.61] | 0.54 [0.47–0.62] | 0.49 [0.42–0.57] | 90.6 |
| MergeMind, lexical backend (ablation) | 149 | 0.48 [0.40–0.56] | 0.74 [0.68–0.82] | 0.79 [0.72–0.85] | 0.68 [0.62–0.75] | 0.61 [0.55–0.67] | 0.72 [0.65–0.78] | 0.68 [0.61–0.75] | 3.2 |
| Baseline: files named in the issue | 149 | 0.07 [0.03–0.11] | 0.08 [0.04–0.13] | 0.08 [0.04–0.13] | 0.07 [0.03–0.12] | 0.08 [0.04–0.12] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 149 | 0.11 [0.07–0.17] | 0.23 [0.16–0.30] | 0.32 [0.25–0.40] | 0.24 [0.18–0.31] | 0.21 [0.16–0.26] | – | – | 0.1 |

Definitions: Hit@k = a fix file is among the top-k predicted files; File-Recall@k = share of fix files in the top k; MRR = 1/rank of the first fix file; Scope coverage = share of fix files inside the scope MergeMind would allow a contributor to touch; Test recall = share of the fix PR's changed test files that MergeMind's impact analysis named.