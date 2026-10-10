# Localization backtest `v1`

Repos: encode/httpx, pydantic/pydantic, pytest-dev/pytest. Issues: 450 total, 412 usable ({'fix files did not exist at the snapshot': 38}).
Leakage control: code checked out at the last default-branch commit before the start of each issue's month; ground truth = source files modified by the merged PR that closed the issue. No LLM is involved in this table. Cells: mean [bootstrap 95% CI].

### Test split (newest 40% per repo, n=166) — headline

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 166 | 0.45 [0.37–0.52] | 0.67 [0.60–0.74] | 0.77 [0.70–0.83] | 0.68 [0.61–0.74] | 0.58 [0.52–0.64] | 0.59 [0.52–0.66] | 0.50 [0.41–0.58] | 106.2 |
| MergeMind, lexical backend (ablation) | 166 | 0.45 [0.37–0.52] | 0.73 [0.66–0.80] | 0.80 [0.73–0.86] | 0.69 [0.62–0.75] | 0.59 [0.53–0.65] | 0.63 [0.57–0.70] | 0.62 [0.55–0.70] | 4.8 |
| Baseline: files named in the issue | 166 | 0.13 [0.08–0.19] | 0.14 [0.09–0.20] | 0.15 [0.10–0.20] | 0.14 [0.09–0.19] | 0.14 [0.09–0.19] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 166 | 0.17 [0.11–0.23] | 0.35 [0.28–0.43] | 0.44 [0.37–0.52] | 0.33 [0.27–0.40] | 0.28 [0.22–0.33] | – | – | 0.3 |

### Dev split (oldest 60%, n=246)

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 246 | 0.42 [0.36–0.48] | 0.65 [0.59–0.71] | 0.76 [0.71–0.81] | 0.65 [0.59–0.70] | 0.55 [0.50–0.60] | 0.55 [0.49–0.61] | 0.41 [0.34–0.47] | 85.3 |
| MergeMind, lexical backend (ablation) | 246 | 0.48 [0.41–0.54] | 0.67 [0.61–0.72] | 0.72 [0.67–0.78] | 0.62 [0.56–0.67] | 0.58 [0.53–0.63] | 0.66 [0.61–0.72] | 0.60 [0.53–0.66] | 4.2 |
| Baseline: files named in the issue | 246 | 0.11 [0.07–0.15] | 0.12 [0.08–0.16] | 0.12 [0.08–0.16] | 0.10 [0.07–0.14] | 0.11 [0.07–0.15] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 246 | 0.17 [0.12–0.22] | 0.33 [0.28–0.40] | 0.42 [0.37–0.49] | 0.32 [0.27–0.37] | 0.28 [0.24–0.33] | – | – | 0.3 |

### All usable issues (n=412)

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 412 | 0.43 [0.38–0.48] | 0.66 [0.61–0.70] | 0.76 [0.72–0.80] | 0.66 [0.62–0.70] | 0.56 [0.52–0.60] | 0.57 [0.52–0.61] | 0.44 [0.39–0.49] | 88.6 |
| MergeMind, lexical backend (ablation) | 412 | 0.47 [0.42–0.51] | 0.69 [0.65–0.73] | 0.75 [0.71–0.79] | 0.65 [0.60–0.68] | 0.58 [0.55–0.62] | 0.65 [0.61–0.69] | 0.61 [0.56–0.66] | 4.5 |
| Baseline: files named in the issue | 412 | 0.12 [0.09–0.15] | 0.13 [0.10–0.16] | 0.13 [0.10–0.17] | 0.12 [0.09–0.15] | 0.12 [0.09–0.15] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 412 | 0.17 [0.13–0.20] | 0.34 [0.30–0.39] | 0.43 [0.39–0.48] | 0.32 [0.28–0.36] | 0.28 [0.25–0.32] | – | – | 0.3 |

### encode/httpx

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 128 | 0.52 [0.43–0.60] | 0.74 [0.66–0.82] | 0.85 [0.79–0.91] | 0.76 [0.70–0.83] | 0.64 [0.58–0.71] | 0.64 [0.56–0.71] | 0.39 [0.30–0.49] | 20.5 |
| MergeMind, lexical backend (ablation) | 128 | 0.57 [0.48–0.66] | 0.82 [0.75–0.88] | 0.84 [0.78–0.91] | 0.73 [0.66–0.79] | 0.69 [0.62–0.76] | 0.72 [0.65–0.79] | 0.74 [0.65–0.82] | 4.3 |
| Baseline: files named in the issue | 128 | 0.09 [0.05–0.15] | 0.12 [0.06–0.18] | 0.12 [0.07–0.19] | 0.11 [0.06–0.16] | 0.11 [0.06–0.16] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 128 | 0.30 [0.22–0.38] | 0.52 [0.43–0.60] | 0.64 [0.55–0.73] | 0.51 [0.44–0.59] | 0.44 [0.38–0.51] | – | – | 0.4 |

### pydantic/pydantic

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 135 | 0.38 [0.30–0.46] | 0.59 [0.50–0.67] | 0.69 [0.61–0.77] | 0.57 [0.50–0.65] | 0.50 [0.44–0.58] | 0.52 [0.45–0.60] | 0.42 [0.33–0.52] | 136.5 |
| MergeMind, lexical backend (ablation) | 135 | 0.36 [0.27–0.44] | 0.51 [0.43–0.59] | 0.63 [0.55–0.70] | 0.52 [0.44–0.60] | 0.46 [0.39–0.53] | 0.51 [0.44–0.59] | 0.42 [0.33–0.51] | 8.8 |
| Baseline: files named in the issue | 135 | 0.19 [0.12–0.25] | 0.19 [0.13–0.26] | 0.19 [0.13–0.26] | 0.17 [0.11–0.23] | 0.19 [0.12–0.26] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 135 | 0.10 [0.05–0.16] | 0.30 [0.22–0.38] | 0.35 [0.27–0.43] | 0.23 [0.17–0.30] | 0.21 [0.16–0.26] | – | – | 0.4 |

### pytest-dev/pytest

| Method | n | Hit@1 | Hit@3 | Hit@5 | File-Recall@5 | MRR | Scope coverage | Test recall | s/issue |
|---|---|---|---|---|---|---|---|---|---|
| MergeMind (entire-graph) | 149 | 0.41 [0.33–0.49] | 0.66 [0.58–0.73] | 0.75 [0.68–0.82] | 0.65 [0.58–0.72] | 0.55 [0.48–0.61] | 0.54 [0.47–0.62] | 0.49 [0.42–0.57] | 90.6 |
| MergeMind, lexical backend (ablation) | 149 | 0.48 [0.40–0.56] | 0.74 [0.68–0.82] | 0.79 [0.72–0.85] | 0.68 [0.62–0.75] | 0.61 [0.55–0.67] | 0.72 [0.65–0.78] | 0.68 [0.61–0.75] | 3.2 |
| Baseline: files named in the issue | 149 | 0.07 [0.03–0.11] | 0.08 [0.04–0.13] | 0.08 [0.04–0.13] | 0.07 [0.03–0.12] | 0.08 [0.04–0.12] | – | – | 0.0 |
| Baseline: most-changed files (180 d) | 149 | 0.11 [0.07–0.17] | 0.23 [0.16–0.30] | 0.32 [0.25–0.40] | 0.24 [0.18–0.31] | 0.21 [0.16–0.26] | – | – | 0.1 |

Definitions: Hit@k = a fix file is among the top-k predicted files; File-Recall@k = share of fix files in the top k; MRR = 1/rank of the first fix file; Scope coverage = share of fix files inside the scope MergeMind would allow a contributor to touch; Test recall = share of the fix PR's changed test files that MergeMind's impact analysis named.