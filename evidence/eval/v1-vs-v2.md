# MergeMind v1 vs v2

Every number is on the **test split** (newest 40% of each repo's issues, never used for tuning) of the same leakage-free backtest: encode/httpx, pydantic/pydantic, pytest-dev/pytest. Both versions score the same issues and PRs, so the CI is a paired bootstrap 95% CI of the difference (2,000 resamples). `*` = the CI excludes 0.

| Measure | n | v1 | v2 | v2 - v1 [95% CI] |
|---|---|---|---|---|
| Localization Hit@1 (fix file ranked first) | 166 | 0.45 | 0.48 | +0.024 [-0.030, +0.084] |
| Localization Hit@3 | 166 | 0.67 | 0.74 | +0.066 [+0.012, +0.127] * |
| Localization Hit@5 | 166 | 0.77 | 0.81 | +0.048 [-0.006, +0.102] |
| Localization MRR | 166 | 0.58 | 0.62 | +0.046 [+0.008, +0.082] * |
| Share of the fix PR's test files that MergeMind names | 132 | 0.50 | 0.74 | +0.242 [+0.176, +0.314] * |
| Safety-scan hold rate on merged fix PRs (lower is better) | 172 | 0.12 | 0.05 | -0.070 [-0.110, -0.035] * |
| Fix PRs touching a source file outside the scope (lower is better) | 170 | 0.58 | 0.47 | -0.112 [-0.159, -0.065] * |
| Triage (n=18 sample): Accepted (all sample issues were real, fixed bugs) | 18 | 0.50 | 0.50 | +0.000 [-0.278, +0.278] |
| Triage (n=18 sample): Investigator localization Hit@1 | 18 | 0.50 | 0.56 | +0.056 [+0.000, +0.167] |
| Triage (n=18 sample): Investigator localization Hit@3 | 18 | 0.72 | 0.67 | -0.056 [-0.167, +0.000] |
| Triage (n=18 sample): Verifier rejected the final report (lower is better) | 18 | 0.06 | 0.11 | +0.056 [-0.111, +0.222] |

Localization rows: n=166 issues. PR rows: merged fix PRs of the test-split issues (all were accepted by maintainers, so every hold is a false alarm a human must clear). Triage rows: the same 18 test-split issues as v1, live Nemotron; with n=18 only large changes can show up as significant.

## Guardrails that must not regress

- Leak corpus (19 adversarial + 7 benign messages): **0 leaks**, 0 false redactions (rules only; `mergemind eval-leak --critic` adds the Nemotron critic).
- Safety-scan unit tests: workflow edits, dependency changes, provider tokens and private keys (also in test files), known secret values, and risky calls in source are still held ([tests/test_v2_precision.py](../../tests/test_v2_precision.py)).

## Triage cost and speed

- Median tokens per issue: 69,380 (v1) vs 55,060 (v2); median wall time 144 s vs 556 s (v2 ran 3 issues in parallel; 60 LLM calls served from the cache).
- Dispositions v1: {'accept': 9, 'needs_info': 3, 'feature_review': 3, 'escalate': 2, 'duplicate': 1}
- Dispositions v2: {'accept': 9, 'feature_review': 4, 'escalate': 3, 'needs_info': 2}

| Issue | v1 | v2 | v2 notes |
|---|---|---|---|
| encode/httpx#1615 | accept | feature_review | feature request (investigator chose needs_info) |
| encode/httpx#2694 | needs_info | accept |  |
| pydantic/pydantic#13148 | escalate | needs_info |  |
| pydantic/pydantic#13647 | duplicate | accept |  |
| pydantic/pydantic#13860 | accept | escalate | verifier rejected the report twice |
| pytest-dev/pytest#14004 | needs_info | accept |  |
| pytest-dev/pytest#14377 | accept | escalate | verifier rejected the report twice |

## What changed in v2 and why (each from a v1 measurement)

| v1 finding | v2 change | Code |
|---|---|---|
| 25% of good PRs held; most holds were false positives ([pr-static-v1.json](pr-static-v1.json)) | risky calls only in source code, the repo's own package and `re.compile` exempt, identifier/URL-aware entropy rule, code expressions and stand-in passwords are not secrets, tool config in pyproject.toml is not a dependency change, pure renames are not binaries | [pr/safety.py](../../src/mergemind/pr/safety.py), [guard/secrets.py](../../src/mergemind/guard/secrets.py) |
| entire-graph and lexical each win on different repos ([locate-v1.md](locate-v1.md)) | weighted RRF of both rankings (weights tuned on dev) | [code/localize.py](../../src/mergemind/code/localize.py) |
| entire-graph finds fewer of the fix's tests (0.44 vs 0.61) | union of both backends' tests | same |
| 61% of fixes touch a file outside the scope | top-5 ranked files + co-changed files + new files next to scoped code (size/coverage knee on dev) | [code/scope.py](../../src/mergemind/code/scope.py) |
| triage escalated real bugs after verifier rejections on mechanical slips (dev-split diagnosis, [triage-dev-v1.log](triage-dev-v1.log)) | decorators are not @mentions; clamp line ranges and fix region evidence references before the rules run (claims stay strict) | [agents/verifier.py](../../src/mergemind/agents/verifier.py) |

Details: [locate-v2.md](locate-v2.md) · [pr-static-v2.json](pr-static-v2.json) · [triage-v2.json](triage-v2.json) · neatlogs runs tagged `eval:v2`.
