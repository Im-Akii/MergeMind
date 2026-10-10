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
| M0 foundation · M1 code intelligence · M2 investigator + verifier · M3 leak guard · M4 access + briefing · M5 PR analysis · M6 merge gate · M7 backtest + recovery | done |
| M8 v2 improvements + packaging | to do |

Results so far (450 real issues, leakage-free): MergeMind puts the fixing file in its top 5 for ~77–80% of issues
vs 44% for the best baseline; full triage costs ~$0.008 per issue at hosted Nemotron prices. See [evidence/](evidence/README.md).

## Setup

```bash
uv sync
```

Create `.env.local` (never committed):

```
NVIDIA_API_KEY=...       # build.nvidia.com
NEATLOGS_API_KEY=...     # optional: tracing
GITHUB_TOKEN=...         # see "Token permissions" below
```

Recommended: install the [Entire CLI](https://docs.entire.io/installation) and its graph plugin. MergeMind then uses
entire-graph for code search, definitions and impact (callers, tests, co-changed files); without it, a git-grep fallback
is used. On the same issue the investigator needed about half the tokens with entire-graph.

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
| `mergemind run --repo o/r` | engine loop, every 15 s | yes |

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
