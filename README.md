# MergeMind

A GitHub agent that takes an issue from **opened** to **merged fix**, with the issue's author as the contributor:

1. **Triage**: reads the issue, investigates the actual code with NVIDIA Nemotron and entire-graph, and decides
   (accept, needs info, duplicate, question, feature review, security, escalate). Every claim cites evidence.
2. **Access**: offers the author time-limited access (`/contribute`) with a starter briefing.
3. **PR analysis**: checks the pull request for conflicts, scope, impact, tests (run in a sandboxed workflow), safety and
   the issue's acceptance criteria, then reports.
4. **Leak guard**: nothing MergeMind posts can reveal secrets, restricted files or out-of-scope code.
5. **Merge** after a maintainer's `/mergemind approve`, re-verified against the latest base and pinned to the approved commit.

The full design is in [buildplan-v2.md](buildplan-v2.md).

## Status

| Milestone | State |
|---|---|
| M0 foundation · M1 code intelligence · M2 investigator + verifier · M3 leak guard · M4 access + briefing · M5 PR analysis · M6 merge gate · M7 backtest + recovery · M8 v2 + packaging | done |

## Results (v2, leakage-free backtest on 450 real issues and 438 merged fix PRs)

Test split (newest 40%, never used for tuning); `[ ]` = paired bootstrap 95% CI of the v2 − v1 difference.
Full table: [evidence/eval/v1-vs-v2.md](evidence/eval/v1-vs-v2.md).

| Measure | v1 | v2 |
|---|---|---|
| Fix file in MergeMind's top 3 | 0.68 | **0.74** [+0.01, +0.13] |
| Fix file in the top 5 (best baseline: 0.44) | 0.77 | **0.81** [−0.01, +0.10] |
| Share of the fix's test files MergeMind names | 0.50 | **0.74** [+0.18, +0.31] |
| Good PRs held by the safety scan (false alarms), test split | 12% | **5%** [−11, −4 pts] |
| Fix PRs touching a source file outside the allowed scope, test split | 58% | **47%** [−16, −7 pts] |
| Full Nemotron triage, 18-issue sample: real bugs accepted | 9/18 | 9/18 (no measurable change) |
| Leak corpus: secrets / restricted content leaked | 0 | **0** |

All 438 PRs: false-alarm holds 25% → 8%, out-of-scope 61% → 45%. LLM cost of a full triage is ~$0.007 per issue at hosted Nemotron list prices ($0 on the free NVIDIA endpoint);
see [evidence/cfo/](evidence/cfo/unit_economics.md) and the live cfo.ai business model (https://cfo.ai/s/PG56dy5YWbPcGD_b). Everything else: [evidence/](evidence/README.md).

## Use MergeMind on your own repository

Step-by-step guide (about 20 minutes, runs free on GitHub Actions): **[SETUP_GUIDE.txt](SETUP_GUIDE.txt)**.

## Setup (development)

```bash
uv sync
```

Create `.env.local` (never committed):

```
NVIDIA_API_KEY=...       # build.nvidia.com
NEATLOGS_API_KEY=...     # optional: tracing
GITHUB_TOKEN=...         # see "Token permissions" below
```

Recommended: install the [Entire CLI](https://docs.entire.io/installation) and its graph plugin. MergeMind then runs
entire-graph (code search, definitions, impact: callers, tests, co-changed files) next to a git-grep backend and fuses
the two rankings; without Entire, only the git-grep backend is used. On the same issue the investigator needed about
half the tokens with entire-graph.

```powershell
irm https://entire.io/install.ps1 | iex      # Windows (macOS: brew install --cask entireio/tap/entire; Linux: curl -fsSL https://entire.io/install.sh | bash)
entire plugin install graph
```

`MERGEMIND_NO_ENTIRE=1` forces the fallback; `ENTIRE_BIN` points at a specific binary.

## Commands

| Command | What it does | Writes to GitHub? |
|---|---|---|
| `mergemind locate <issue-url>` | localize an issue in the code (no LLM) | no |
| `mergemind triage <issue-url>` | full triage with Nemotron | no |
| `mergemind eval-leak [--critic]` | run the seeded leak corpus | no |
| `mergemind sandbox-kit DIR -m <you>` | write a ready-to-push test repo | no |
| `mergemind preflight --repo o/r` | check token rights, branch protection, workflow, policy | no |
| `mergemind tick --repo o/r [--dry-run]` | one engine pass | yes (unless `--dry-run` or `autonomy: observe`) |
| `mergemind run --repo o/r` | engine loop, every 15 s (laptop mode) | yes |
| `mergemind actions-kit [DIR]` | write the always-on GitHub Actions runtime into a repo | no |
| `mergemind eval dataset\|locate\|locate-v2\|triage\|pr-static\|compare\|economics` | the backtests behind `evidence/` | no |

## Always-on with GitHub Actions

`mergemind actions-kit .` writes `.github/workflows/mergemind.yml`: one `tick` per issue, comment, finished
verify run, and every 10 minutes. State (SQLite) lives on an orphan `mergemind-state` branch, and one run at a
time is allowed. The API keys go in a GitHub **Environment** named `mergemind` that is limited to the default
branch. Contributors who get push access can run workflows on their own branches, but those runs can't read the
keys. The workflow has no `pull_request` trigger and never checks out PR code; PR tests stay in
`mergemind-verify.yml`, which has no secrets. Environment secrets: `MERGEMIND_TOKEN` (see below), `NVIDIA_API_KEY`
and, optionally, `NEATLOGS_API_KEY`. Use either this or a laptop `mergemind run` for a repo, never both.

## Token permissions (fine-grained, one repository only)

Contents, Issues, Pull requests, Commit statuses, Administration: **read & write**. Actions, Metadata: **read**.
Administration is only needed to invite and remove collaborators (`access.mode: collaborator`); `fork` mode works without it.

## Testing end to end

```bash
uv run mergemind sandbox-kit ../mergemind-sandbox -m <your-github-username>
```

then follow `KIT_STEPS.md` in that folder: push it to a new public repo, protect `main`, run `preflight`, start the engine,
open the sample issue from a second account, `/contribute`, then push the two sample fixes as a PR.

## Tests

```bash
uv run pytest -q
```

## Safety model

- The LLM only has read-only tools; all writes go through a rule-based policy gate.
- Issue and PR text are untrusted data; restricted files (`.env`, `deploy/`, keys…) are never shown to the LLM.
- PR code only runs in `.github/workflows/mergemind-verify.yml` (`pull_request`, read-only token, no secrets).
- `/mergemind stop` and `MERGEMIND_AUTONOMY=observe` are kill switches.
