# Entire evidence

Entire plays two roles in MergeMind:
- **At runtime,** entire-graph is MergeMind's code-intelligence backend.
- **While building MergeMind,** Entire checkpoints recorded every coding session.

## entire-graph found a real bug in MergeMind

On Oct 11, a live MergeMind briefing on [AfreenInnovates/campus-evac#2](https://github.com/AfreenInnovates/campus-evac/issues/2)
was held by the leak guard by mistake. We asked entire-graph about it, running on the code from before the fix:

```bash
entire graph search --repo . --profile full --query "briefing held by the leak guard: secret scanner flags GitHub blob links with an uppercase owner name as high-entropy secrets"
```

**Rank 1 was `SecretScanner.scan` in `src/mergemind/guard/secrets.py`, the exact function we then fixed.** The
graph supported that ranking with call-graph signals (`graph:calls`, `graph:callers`), and it also named the covering
test to run. Full output: [graph-search-leak-bug.json](graph-search-leak-bug.json).

Before changing the function, we checked its impact:

```bash
entire graph impact --repo . --symbol SecretScanner.scan
```

It reported 6 direct callers (including the PR safety scan and the leak guard's `redact`), 5 callees, 2 type
consumers, 3 co-change files and 4 siblings. That told us which tests to run. Full output:
[graph-impact-scan.txt](graph-impact-scan.txt).

The same `search` and `impact` calls run inside MergeMind on every issue. Impact analysis sets each contributor's
scope, as screenshot 04 in [../neatlogs/](../neatlogs/README.md) shows.

## Measured value

- **450-issue ablation:** entire-graph versus keyword search, in [../eval/locate-v1.md](../eval/locate-v1.md). Each won
  on different repositories.
- **v2 fusion:** combining the two rankings raised top-3 accuracy from 0.68 to 0.74 and test recall from 0.50 to 0.74,
  in [../eval/locate-v2.md](../eval/locate-v2.md).
- **Token savings:** on the same issue, the investigator needed about half the tokens with entire-graph.

## Checkpoints

`entire enable` recorded our coding sessions. [checkpoints.txt](checkpoints.txt) is the output of
`entire checkpoint list`. It lists 12 checkpoints, each tied to a commit made during the hackathon, Oct 10–11, 2026.

> [!NOTE]
> The checkpoint hashes come from our development repository. The public history is re-committed from it,
> sometimes combining several local commits into one, so its hashes differ. Most checkpoints match a public commit
> by message and time.
