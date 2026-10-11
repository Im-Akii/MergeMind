// Everything the page says, in one place. Facts come from the MergeMind source:
// src/mergemind/config.py, cli.py, github/commands.py, templates/*.yml, sandbox_kit.py.

export const REPO_URL = "https://github.com/Im-Akii/MergeMind";
export const RAW_URL = "https://raw.githubusercontent.com/Im-Akii/MergeMind/main";

export const stations = [
  {
    id: "triage",
    name: "Triage",
    actor: "MergeMind",
    body:
      "Reads the new issue and investigates the actual code. It decides accept, needs info, duplicate, question, feature, security or escalate, and every claim cites a file and line.",
  },
  {
    id: "access",
    name: "Access",
    actor: "Reporter",
    body:
      "On an accepted bug the reporter comments /contribute. They get push access for 14 days, or work from a fork, plus a briefing: where the bug is, what's in scope, what done means.",
  },
  {
    id: "check",
    name: "PR check",
    actor: "MergeMind",
    body:
      "Conflicts, scope, impact on callers, a sandboxed test run where the new test must fail before the fix and pass after it, a safety scan, and each acceptance criterion.",
  },
  {
    id: "guard",
    name: "Leak guard",
    actor: "MergeMind",
    body:
      "Every comment goes through a secret scanner, a restricted-path filter and a second-model critic that fails closed. @mentions are escaped so nobody gets pinged by accident.",
  },
  {
    id: "merge",
    name: "Merge gate",
    actor: "Maintainer",
    body:
      "A maintainer comments /mergemind approve. MergeMind re-verifies that exact commit against the latest base, merges it, and opens a revert PR if the default branch's CI breaks.",
  },
] as const;

export const parts = [
  { item: "A GitHub repository you administer", why: "MergeMind works on its issues and pull requests. Public repos get Actions minutes for free.", need: "required" },
  { item: "A fine-grained GitHub token", why: "Posts comments, invites the reporter, merges. Scoped to that one repository.", need: "required" },
  { item: "An NVIDIA API key", why: "From build.nvidia.com. Nemotron models do the triage, the PR criteria check and the leak critic. The free endpoint costs $0.", need: "required" },
  { item: "Python 3.12 and uv (or pip)", why: "To run preflight, actions-kit and laptop mode on your machine.", need: "required" },
  { item: "A second GitHub account", why: "Plays the reporter while you test, so you can see both sides.", need: "for testing" },
  { item: "Entire CLI with the graph plugin", why: "Better code search and impact analysis. Without it MergeMind uses git grep.", need: "optional" },
  { item: "A neatlogs API key", why: "One trace per issue and pull request. Tracing is off without it.", need: "optional" },
] as const;

export type Access = "none" | "read" | "write";
export const tokenPerms: { name: string; level: Access; note: string }[] = [
  { name: "Contents", level: "write", note: "read code, push the state branch, merge" },
  { name: "Issues", level: "write", note: "triage cards and briefings" },
  { name: "Pull requests", level: "write", note: "reviews, verdicts, revert PRs" },
  { name: "Commit statuses", level: "write", note: "the mergemind/verify status" },
  { name: "Administration", level: "write", note: "invite and remove collaborators (collaborator mode only)" },
  { name: "Actions", level: "read", note: "read the mergemind-verify results" },
  { name: "Metadata", level: "read", note: "added automatically" },
];

export const starterPolicy = `# .github/mergemind.yml
autonomy: suggest          # observe | suggest | invite | merge
maintainers: [your-username]
escalate_to: [your-username]
access:
  mode: collaborator       # collaborator | fork
  ttl: 14d
  min_account_age: 30d
pr:
  require_regression_test: true
  merge_method: squash
guard:
  sensitive_paths: [".env*", "**/.env*", "**/*.pem", "**/*.key", "**/secrets/**", "deploy/**", "infra/**"]
  maintainer_channel: null # e.g. you/project-mergemind (a private repo)`;

export const envLocal = `NVIDIA_API_KEY=nvapi-...
GITHUB_TOKEN=github_pat_...
NEATLOGS_API_KEY=          # optional`;

export type Step = {
  id: string;
  title: string;
  lead: string;
  code?: { label: string; text: string }[];
  list?: string[];
  note?: string;
  perms?: boolean;
  tip: string; // what Scout says while you're on this step
};

export const steps: Step[] = [
  {
    id: "install",
    tip: "Finish with uv run mergemind --help. If it prints a list of commands, I'm installed.",
    title: "Install MergeMind on your machine",
    lead: "You need the CLI for preflight, for writing the workflow, and for laptop mode.",
    code: [
      { label: "clone (recommended)", text: `git clone ${REPO_URL}.git\ncd MergeMind\nuv sync\nuv run mergemind --help` },
      { label: "or pip", text: `pip install "git+${REPO_URL}.git"\nmergemind --help` },
    ],
  },
  {
    id: "token",
    tip: "GitHub shows the token only once, so copy it before you leave that page. Tick exactly the seven boxes on the card, nothing more.",
    title: "Create the GitHub token",
    lead: "Open github.com/settings/personal-access-tokens and choose Generate new token (fine-grained).",
    list: [
      "Token name: mergemind. Pick an expiration and write it down. An expired token answers 401 Bad credentials.",
      "Resource owner: the account or organization that owns the repository. Organizations may need an owner to approve the token.",
      "Repository access: Only select repositories, then pick your repository.",
      "Repository permissions: exactly the seven below. Leave everything else at No access.",
    ],
    perms: true,
    note:
      "Don't select every permission. The token can do anything you grant, and MergeMind needs only these. Editing permissions later keeps the same token value; regenerating gives you a new one. MergeMind's comments appear under the token owner's name, so use a separate bot account if you'd rather not post as yourself.",
  },
  {
    id: "policy",
    tip: "Leave autonomy on suggest for now. I'll post what I find, and I'll ask a maintainer before I give anyone access.",
    title: "Write the policy file",
    lead: "Commit .github/mergemind.yml to the default branch. Every key is optional; start on suggest and raise autonomy once you trust it.",
    code: [{ label: ".github/mergemind.yml", text: starterPolicy }],
  },
  {
    id: "workflows",
    tip: "Both files have to land on your default branch. That's the only branch I run from.",
    title: "Add the two workflows",
    lead: "Run these from the root of a local clone of your repository, then commit and push both files to the default branch.",
    code: [
      {
        label: "PR tests, no secrets",
        text: `mkdir -p .github/workflows\ncurl -fsSL ${RAW_URL}/src/mergemind/templates/mergemind-verify.yml \\\n  -o .github/workflows/mergemind-verify.yml`,
      },
      { label: "the always-on runtime", text: "mergemind actions-kit .\n# or, from the MergeMind clone: uv run mergemind actions-kit ../your-repo" },
    ],
    note:
      "mergemind-verify.yml runs contributor code with a read-only token and no secrets, and it runs pytest. For another stack, change its Install and test steps but keep the pull_request trigger. mergemind.yml runs one engine pass per issue, comment, finished test run, and every 10 minutes.",
  },
  {
    id: "environment",
    tip: "The name has to be exactly mergemind, all lowercase. That's the environment my workflow asks for.",
    title: "Create the mergemind environment",
    lead: "Settings → Environments → New environment, named mergemind.",
    list: [
      "Deployment branches and tags: Selected branches, then add your default branch only.",
      "Environment secret MERGEMIND_TOKEN: the token from step 2.",
      "Environment secret NVIDIA_API_KEY: your key from build.nvidia.com.",
      "Environment secret NEATLOGS_API_KEY: optional.",
      "Optional repository variable MERGEMIND_REF: a MergeMind commit SHA, to freeze the version you run.",
    ],
    note:
      "Contributors that MergeMind invites can run workflows on their own branches. Limiting the environment to the default branch keeps the keys out of those runs.",
  },
  {
    id: "ruleset",
    tip: "No ruleset, no invites. I check it every time before I give someone access.",
    title: "Protect the default branch",
    lead: "Settings → Rules → Rulesets → New branch ruleset. Target the default branch.",
    list: [
      "Require a pull request before merging, with 1 required approval.",
      "Block force pushes.",
      "Restrict deletions.",
      "Enforcement status: Active.",
      "Optional: require the mergemind/verify status check.",
    ],
    note: "MergeMind refuses to give anyone access while this ruleset is missing, and tells the maintainer why.",
  },
  {
    id: "preflight",
    tip: "Every line should say PASS. If one says FAIL, find it under Fault codes further down; each has a fix.",
    title: "Run preflight",
    lead: "It reads your repository with the token and checks everything above. Nothing is written.",
    code: [
      { label: "PowerShell", text: `$env:GITHUB_TOKEN="github_pat_..."\nuv run mergemind preflight --repo owner/repo` },
      { label: "macOS / Linux", text: `export GITHUB_TOKEN="github_pat_..."\nuv run mergemind preflight --repo owner/repo` },
    ],
    note: "Every line should say PASS, and the last line ready. A FAIL line names the problem; the fault codes below say how to fix each one.",
  },
  {
    id: "start",
    tip: "The first run takes a minute or two while I install. After that I wake up on every issue and comment by myself.",
    title: "Start it",
    lead: "Open your repository's Actions tab, pick mergemind, and choose Run workflow once. After that it runs by itself.",
    code: [
      { label: "or laptop mode: .env.local in the MergeMind folder", text: envLocal },
      { label: "then", text: "uv run mergemind run --repo owner/repo" },
    ],
    note: "Use one engine per repository. If Actions and a laptop run both poll the same repository, every comment is posted twice.",
  },
  {
    id: "test",
    tip: "Open the sample issue from your second account. That account plays the reporter, so that's who I'll invite.",
    title: "Test it end to end",
    lead: "The sandbox kit writes a tiny repository with a planted bug, a sample issue and two prepared fixes.",
    code: [{ label: "sandbox", text: "uv run mergemind sandbox-kit ../mergemind-sandbox -m your-username" }],
    list: [
      "Push it to a new public repository and protect main.",
      "Run preflight, then start the engine.",
      "From your second account, open the issue in KIT_ISSUE.md and comment /contribute.",
      "Push KIT_FIX_NO_TEST.patch first: expect changes requested. Then the full fix: expect verification passed.",
      "As the maintainer, comment /mergemind approve. MergeMind re-checks and merges.",
    ],
  },
];

export const autonomy = [
  { id: "observe", label: "Observe", body: "Writes nothing to GitHub. It only reports what it would do, so you can watch it on a real repository first.", mood: "sleepy" },
  { id: "suggest", label: "Suggest", body: "Posts triage cards and PR verdicts. Giving a reporter access needs a maintainer's /mergemind approve-access. This is the default.", mood: "surprised" },
  { id: "invite", label: "Invite", body: "Grants access by itself after /contribute, and posts pull request reviews.", mood: "wink" },
  { id: "merge", label: "Merge", body: "Also merges after /mergemind approve and a fresh verification, revokes access when the work is done, and opens revert PRs when CI breaks.", mood: "delighted" },
] as const;

export const configKeys: { key: string; def: string; body: string }[] = [
  { key: "autonomy", def: "suggest", body: "observe, suggest, invite or merge. The MERGEMIND_AUTONOMY variable can lower it, never raise it." },
  { key: "maintainers", def: "[]", body: "GitHub usernames to ping when a decision is needed and escalate_to is empty." },
  { key: "escalate_to", def: "[]", body: "Who gets escalations: security reports, safety holds, failed merges." },
  { key: "access.mode", def: "collaborator", body: "collaborator sends a push-access invite (needs Administration on the token). fork means the reporter forks; no invite." },
  { key: "access.permission", def: "push", body: "push or triage." },
  { key: "access.ttl", def: "14d", body: "How long access lasts. It is also revoked on merge or when the issue closes." },
  { key: "access.checkin_at", def: "50%", body: "When to check in if there's no branch or PR activity yet." },
  { key: "access.min_account_age", def: "30d", body: "Accounts younger than this can't get access." },
  { key: "access.max_active_leases_per_person", def: "1", body: "How many issues one person can hold at once." },
  { key: "pr.require_regression_test", def: "true", body: "The PR must add a test that fails on the base branch and passes with the fix." },
  { key: "pr.test_commands", def: "[]", body: "Shown in the briefing. Detected from the repository when empty." },
  { key: "pr.allow_out_of_scope", def: "flag", body: "flag notes out-of-scope files for the maintainer; block requests changes." },
  { key: "pr.merge_method", def: "squash", body: "squash, merge or rebase." },
  { key: "pr.ci_timeout", def: "30m", body: "How long to wait for mergemind-verify results." },
  { key: "pr.post_merge_watch", def: "30m", body: "How long to watch the default branch's CI after a merge." },
  { key: "guard.sensitive_paths", def: ".env*, **/*.pem, deploy/** …", body: "Never shown to the model and never quoted. Values found in these files are redacted from everything MergeMind posts." },
  { key: "guard.maintainer_channel", def: "null", body: "A private repository (owner/name) for held messages and security alerts. Without it they stay in the local log." },
  { key: "demo.time_scale", def: "1.0", body: "Multiplies every timer. Useful for live demos." },
];

export const commands: { cmd: string; who: string; body: string }[] = [
  { cmd: "/contribute", who: "the reporter", body: "Ask for access to fix this issue." },
  { cmd: "/mergemind withdraw", who: "the contributor", body: "Give up the issue. Access is removed." },
  { cmd: "/mergemind recheck", who: "contributor or maintainer", body: "Analyse the pull request again." },
  { cmd: "/mergemind why", who: "anyone", body: "Post the reasoning behind the decision, with evidence, through the leak guard." },
  { cmd: "/mergemind approve-access", who: "maintainer", body: "Allow access on suggest autonomy." },
  { cmd: "/mergemind approve", who: "maintainer", body: "Re-verify and merge the pull request." },
  { cmd: "/mergemind request-changes <text>", who: "maintainer", body: "Send the PR back with your note." },
  { cmd: "/mergemind accept", who: "maintainer", body: "Accept a feature request or an escalated issue." },
  { cmd: "/mergemind not-duplicate", who: "maintainer", body: "Overrule a duplicate suggestion." },
  { cmd: "/mergemind revoke", who: "maintainer", body: "Remove the contributor's access now." },
  { cmd: "/mergemind stop", who: "maintainer", body: "Kill switch. MergeMind stops acting on this issue." },
];

export const envVars: { name: string; body: string }[] = [
  { name: "NVIDIA_API_KEY", body: "Required. In Actions: the NVIDIA_API_KEY environment secret." },
  { name: "GITHUB_TOKEN / GH_TOKEN", body: "Required. In Actions it comes from the MERGEMIND_TOKEN environment secret." },
  { name: "NEATLOGS_API_KEY", body: "Optional tracing." },
  { name: "LLM_BASE_URL", body: "Default https://integrate.api.nvidia.com/v1. Any OpenAI-compatible endpoint." },
  { name: "LLM_MODEL_INVESTIGATOR", body: "Default nvidia/nemotron-3-super-120b-a12b." },
  { name: "LLM_MODEL_FAST", body: "Default nvidia/nemotron-3.5-lightning-30b-a3b." },
  { name: "LLM_RPM", body: "Requests per minute to the model API. Default 30." },
  { name: "MERGEMIND_AUTONOMY", body: "Kill switch. Lowers every repository's autonomy." },
  { name: "MERGEMIND_POLL_S", body: "Laptop mode polling interval in seconds. Default 15." },
  { name: "MERGEMIND_DB", body: "SQLite state file. Default mergemind.db." },
  { name: "MERGEMIND_NO_ENTIRE", body: "Set to 1 to skip entire-graph and use git grep only." },
  { name: "ENTIRE_BIN", body: "Path to a specific Entire binary." },
];

export const faults: { code: string; cause: string; fix: string }[] = [
  {
    code: "401 Bad credentials",
    cause: "GitHub doesn't recognise the token value. It expired, was regenerated, or was pasted with a missing character, quotes or a trailing space.",
    fix: "Copy the current value into .env.local or the MERGEMIND_TOKEN secret. Changing permissions never fixes a 401.",
  },
  {
    code: "403 Resource not accessible by personal access token",
    cause: "The token works but doesn't include this repository or one of the permissions.",
    fix: "Edit the token, add the repository or permission, and click Update. The value stays the same.",
  },
  {
    code: "FAIL  token can invite and remove collaborators",
    cause: "Administration is not set to Read and write.",
    fix: "Add it, or set access.mode: fork so MergeMind never needs to invite anyone.",
  },
  {
    code: "FAIL  `main` is protected: missing …",
    cause: "The ruleset is missing a rule, or its enforcement status isn't Active.",
    fix: "Go back to step 6. The FAIL line names each missing rule.",
  },
  {
    code: "FAIL  mergemind-verify workflow is on the default branch",
    cause: "The file isn't committed to the default branch yet.",
    fix: "Push .github/workflows/mergemind-verify.yml to the default branch.",
  },
  {
    code: "FAIL  mergemind-verify must use `pull_request`",
    cause: "The workflow was changed to pull_request_target, which runs contributor code with access to secrets.",
    fix: "Restore the template's pull_request trigger.",
  },
  {
    code: "Engine pass fails: no NVIDIA key or token",
    cause: "The secrets were added as repository secrets, or the environment isn't named exactly mergemind.",
    fix: "Add them under Settings → Environments → mergemind.",
  },
  {
    code: "Every comment appears twice",
    cause: "A laptop run and the Actions runtime are both working on the same repository.",
    fix: "Stop one of them.",
  },
  {
    code: "Blocked: MergeMind lacks a GitHub permission it needs",
    cause: "A write failed with 403 during a run.",
    fix: "Run preflight to see which permission, add it to the token, then comment /mergemind recheck.",
  },
];

export const safety = [
  "The model only has read-only tools. Every write to GitHub goes through a rule-based policy gate.",
  "Issue and pull request text is data, never instructions.",
  "Restricted files (.env, deploy/, keys) are never shown to the model.",
  "Contributor code only runs in mergemind-verify.yml: pull_request trigger, read-only token, no secrets.",
  "A merge is pinned to the exact commit the maintainer approved. If the branch moved, GitHub refuses it.",
  "/mergemind stop and MERGEMIND_AUTONOMY=observe stop it at any time.",
];

// Ideas taken from CodeRabbit's feature set. None of these exist in MergeMind yet.
export const bench = [
  {
    name: "A walkthrough on every PR",
    from: "CodeRabbit's walkthrough comment: summary, table of changed files, sequence diagram.",
    here: "MergeMind already knows callers, tests and co-changed files from entire-graph. It could draw the call path the fix touches.",
  },
  {
    name: "Learnings",
    from: "CodeRabbit remembers preferences from review chats and applies them next time.",
    here: "MergeMind records every maintainer decision for evaluation. Feed request-changes notes and not-duplicate overrides back into triage.",
  },
  {
    name: "/mergemind config",
    from: "@coderabbitai configuration prints the settings in effect.",
    here: "Print the effective policy, including any lowered autonomy, as a comment.",
  },
  {
    name: "/mergemind pause and resume",
    from: "CodeRabbit can pause reviews on a busy PR.",
    here: "Today the only brake is stop. A pause that keeps state would be gentler.",
  },
  {
    name: "A schema for mergemind.yml",
    from: "CodeRabbit publishes a JSON schema for .coderabbit.yaml.",
    here: "Generate one from the pydantic Policy model so editors autocomplete and validate the file.",
  },
  {
    name: "Finishing touches",
    from: "CodeRabbit offers to write docstrings and unit tests from a checkbox.",
    here: "Offer the contributor a regression-test skeleton built from the briefing's test hint.",
  },
];
