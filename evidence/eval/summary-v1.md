# Backtest v1: summary and what v2 should fix

All numbers come from leakage-free backtests on **450 real issues** (412 usable) from
**encode/httpx, pydantic/pydantic and pytest-dev/pytest**, each closed by exactly one merged
PR. Code is checked out at the last default-branch commit before the issue's month; ground
truth is the fix PR. Headline numbers are on the **test split** (newest 40% per repo, never
used for tuning). Cells are mean [bootstrap 95% CI].

Detailed tables: [locate-v1.md](locate-v1.md) · [pr-static-v1.json](pr-static-v1.json) ·
[triage-v1.json](triage-v1.json) · recovery: [../recovery.md](../recovery.md) ·
unit economics: [../cfo/unit_economics.md](../cfo/unit_economics.md)

## 1. Localization (no LLM): MergeMind finds the fix file

| Test split, n=166 | Hit@1 | Hit@5 | MRR |
|---|---|---|---|
| MergeMind (entire-graph) | 0.45 [0.37–0.52] | 0.77 [0.70–0.83] | 0.58 |
| MergeMind, lexical backend | 0.45 [0.37–0.52] | 0.80 [0.73–0.86] | 0.59 |
| Baseline: files named in the issue | 0.13 | 0.15 | 0.14 |
| Baseline: most-changed files | 0.17 | 0.44 | 0.28 |

- MergeMind beats both baselines by a wide margin (Hit@5 0.77–0.80 vs 0.15 and 0.44; CIs don't overlap).
- **entire-graph vs lexical (ablation): no overall difference in v1.** Per repo it splits:
  entire is better on the largest repo (pydantic Hit@5 0.69 vs 0.63, Hit@3 0.59 vs 0.51),
  lexical is better on pytest (0.79 vs 0.75) and they tie on httpx.
- entire-graph's impact analysis finds fewer of the tests a fix touches (test recall 0.44 vs
  0.61 overall) because it lists call-graph callers only.
- entire-graph is much slower on large repos in this setup (median ~90 s per issue vs ~5 s),
  dominated by one-off index builds per historical snapshot.

## 2. PR side (no CI, no LLM): the safety scan is too noisy

On the 438 merged fix PRs (all accepted by maintainers):

- **25% would have been held** for a maintainer (httpx 44%, pytest 19%, pydantic 11%).
  Most holds are false positives: a library's own name (`httpx.`) counted as a network call,
  `re.compile(` matched as `compile(`, docs files scanned for risky calls, and fake credentials
  in test fixtures flagged as secrets.
- Only 2 fix PRs were later reverted; neither was flagged. Too few to measure catch rate.
- **61% of real fixes touch a source file outside the scope** MergeMind would have allowed
  (flag only, never a block): the scope is too narrow.

## 3. Full triage with Nemotron (sample)

18 test-split issues (6 per repo), live Nemotron Super (investigator) + Lightning (checks),
issue as opened (no comments), code at the snapshot. Details: [triage-v1.json](triage-v1.json),
[triage-v1.log](triage-v1.log).

| Measure | All | httpx | pydantic | pytest |
|---|---|---|---|---|
| Accepted | 9/18 | 5/6 | 2/6 | 2/6 |
| Investigator's localization Hit@1 / Hit@3 | 0.50 / 0.72 | 0.67 / 1.00 | see json | see json |
| Fix files inside the allowed scope (accepted issues) | 0.83 | 1.00 | | |
| Verifier rejected / repair needed | 6% / 22% | 0% / 0% | | |
| Tokens per issue, median / p90 | 69k / 87k | 66k / 77k | | |
| Wall time per issue, median / p90 | 2.4 / 3.3 min | 1.3 / 3.0 min | | |

Every issue in the sample was a real bug that got fixed, so "needs info" (3), "escalate" (2) and
"feature review" (3) are cautious misses (a human is asked), not harmful actions. One
"duplicate" (pydantic#13647) is probably a false duplicate and should be inspected in neatlogs.
The sample is small (n=18): treat these as indicative, not precise.

Cost (see [../cfo/unit_economics.md](../cfo/unit_economics.md)): about **$0.008 per triaged
issue (p90 $0.012)** at hosted Nemotron list prices; $0 on the free NVIDIA endpoint. The PR-review
cost is measured on only 2 small sandbox PRs, so it is a lower bound for real PRs.

## 4. Recovery

All injected faults (GitHub 500/429/403, Nemotron 429, malformed tool calls, graph timeout)
are recovered from as designed; see [../recovery.md](../recovery.md).

## v2 candidates (M8), ranked by expected impact

1. **Safety scan precision**: skip docs and tests for risky-call rules, never flag the repo's
   own package name, require a call boundary for `compile(`/`eval(` (not `re.compile`), and
   apply the secret scanner's high-entropy rule only outside tests. The same scanner also
   flagged long snake_case test names (entropy) and `tokens=70006/6553` (assigned-secret rule)
   when checking these evidence files before publishing. Target: hold rate < 10% without
   missing the planted cases in the leak corpus.
2. **Fuse both backends** for localization (RRF over entire-graph and lexical results): each
   wins on different repos.
3. **Test discovery**: union entire-graph callers with lexical test mentions (lexical test
   recall is 0.61 vs 0.44).
4. **Wider scope**: add co-changed files and same-package siblings to the allowed scope.
5. **Speed**: warm one graph index per repo HEAD in live use; in backtests, reuse indexes
   across nearby snapshots.
