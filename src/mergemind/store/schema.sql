PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repos (
    id              INTEGER PRIMARY KEY,
    full_name       TEXT NOT NULL UNIQUE,
    default_branch  TEXT,
    clone_path      TEXT,
    head_sha        TEXT,
    profile_json    TEXT,
    profile_sha     TEXT,
    policy_json     TEXT,
    issues_cursor   TEXT,
    issues_etag     TEXT,
    pulls_cursor    TEXT,
    pulls_etag      TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    repo_id               INTEGER NOT NULL REFERENCES repos(id),
    number                INTEGER NOT NULL,
    state_json            TEXT NOT NULL,      -- serialized IssueState
    phase                 TEXT NOT NULL,
    phase_version         INTEGER NOT NULL DEFAULT 0,
    card_comment_id       INTEGER,
    last_seen_updated_at  TEXT,
    etag                  TEXT,
    locked_until          REAL,
    PRIMARY KEY (repo_id, number)
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    repo_id       INTEGER NOT NULL REFERENCES repos(id),
    number        INTEGER NOT NULL,
    source        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    external_id   TEXT NOT NULL UNIQUE,
    payload_json  TEXT NOT NULL,
    received_at   REAL NOT NULL,
    processed_at  REAL
);
CREATE INDEX IF NOT EXISTS events_pending ON events(processed_at, id);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    repo_id         INTEGER,
    number          INTEGER,
    kind            TEXT,            -- triage | pr_analysis | merge
    event_id        INTEGER,
    agent_version   TEXT,
    prompt_version  TEXT,
    model           TEXT,
    as_of_sha       TEXT,
    started_at      REAL,
    ended_at        REAL,
    status          TEXT,
    in_tok          INTEGER DEFAULT 0,
    out_tok         INTEGER DEFAULT 0,
    est_cost_usd    REAL DEFAULT 0,
    trace_id        TEXT,
    report_json     TEXT,
    verdict_json    TEXT,
    eval_run_id     TEXT
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key             TEXT PRIMARY KEY,
    stage           TEXT,
    model           TEXT,
    prompt_version  TEXT,
    response_json   TEXT NOT NULL,
    created_at      REAL
);

CREATE TABLE IF NOT EXISTS evidence (
    id         TEXT PRIMARY KEY,
    run_id     INTEGER,
    kind       TEXT NOT NULL,
    locator    TEXT NOT NULL,
    sha        TEXT,
    excerpt    TEXT,
    score      REAL,
    sensitive  INTEGER NOT NULL DEFAULT 0,
    meta_json  TEXT
);

CREATE TABLE IF NOT EXISTS access_leases (
    id            INTEGER PRIMARY KEY,
    repo_id       INTEGER NOT NULL,
    issue_number  INTEGER NOT NULL,
    login         TEXT NOT NULL,
    mode          TEXT NOT NULL,     -- collaborator | fork
    status        TEXT NOT NULL,     -- invited | active | expired | revoked | merged
    invited_at    REAL,
    accepted_at   REAL,
    expires_at    REAL,
    nudged_at     REAL,
    revoked_at    REAL
);

CREATE TABLE IF NOT EXISTS guard_log (
    id             INTEGER PRIMARY KEY,
    run_id         INTEGER,
    message_kind   TEXT,
    decision       TEXT,             -- passed | redacted | held_for_maintainer
    findings_json  TEXT,
    created_at     REAL
);

CREATE TABLE IF NOT EXISTS actions (
    id            INTEGER PRIMARY KEY,
    idem_key      TEXT NOT NULL UNIQUE,
    repo_id       INTEGER,
    number        INTEGER,
    kind          TEXT NOT NULL,
    payload_json  TEXT,
    status        TEXT NOT NULL,     -- pending | done | failed | cancelled
    attempts      INTEGER NOT NULL DEFAULT 0,
    error_class   TEXT,
    github_ref    TEXT,
    created_at    REAL,
    executed_at   REAL
);

CREATE TABLE IF NOT EXISTS timers (
    id        INTEGER PRIMARY KEY,
    due_at    REAL NOT NULL,
    kind      TEXT NOT NULL,
    ref_json  TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'pending'   -- pending | fired | cancelled
);
CREATE INDEX IF NOT EXISTS timers_due ON timers(status, due_at);

CREATE TABLE IF NOT EXISTS gh_cache (
    key         TEXT PRIMARY KEY,
    etag        TEXT,
    body        TEXT,
    fetched_at  REAL
);

-- every issue of a repo (open and closed) for duplicate retrieval
CREATE TABLE IF NOT EXISTS issue_index (
    repo_id       INTEGER NOT NULL,
    number        INTEGER NOT NULL,
    title         TEXT,
    body          TEXT,
    state         TEXT,
    state_reason  TEXT,
    labels_json   TEXT,
    created_at    TEXT,
    closed_at     TEXT,
    updated_at    TEXT,
    signature     TEXT,
    PRIMARY KEY (repo_id, number)
);

-- cached repo brief per head sha
CREATE TABLE IF NOT EXISTS repo_profiles (
    repo_id     INTEGER NOT NULL,
    sha         TEXT NOT NULL,
    brief       TEXT NOT NULL,
    meta_json   TEXT,
    created_at  REAL,
    PRIMARY KEY (repo_id, sha)
);

-- which issue a pull request belongs to
CREATE TABLE IF NOT EXISTS pr_links (
    repo_id       INTEGER NOT NULL,
    pr_number     INTEGER NOT NULL,
    issue_number  INTEGER NOT NULL,
    PRIMARY KEY (repo_id, pr_number)
);
