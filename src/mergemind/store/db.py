"""SQLite store: events, issue state with compare-and-swap, timers, caches, ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterator

from mergemind.models import Event, Evidence, IssueState


class VersionConflict(Exception):
    """Someone else committed this issue's state first."""


class Store:
    """One SQLite connection per thread (WAL lets them read and write concurrently); sharing a
    connection between threads breaks Python's statement cache under load."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        self._main = self._connect()
        schema = resources.files("mergemind.store").joinpath("schema.sql").read_text()
        self._main.executescript(schema)
        self._local.conn = self._main

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            # an in-memory database exists only on its own connection: share it (single-threaded tests)
            c = self._main if self.path == ":memory:" else self._connect()
            self._local.conn = c
        return c

    def close(self) -> None:
        self._main.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ------------------------------------------------------------------ repos

    def repo_id(self, full_name: str) -> int:
        self.conn.execute("INSERT OR IGNORE INTO repos(full_name) VALUES (?)", (full_name,))
        return int(self.conn.execute("SELECT id FROM repos WHERE full_name=?", (full_name,)).fetchone()["id"])

    def repo_get(self, full_name: str, field: str) -> Any:
        row = self.conn.execute(
            f"SELECT {field} FROM repos WHERE full_name=?", (full_name,)
        ).fetchone()
        return row[field] if row else None

    def repo_set(self, full_name: str, **fields: Any) -> None:
        self.repo_id(full_name)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE repos SET {cols} WHERE full_name=?", (*fields.values(), full_name)
        )

    # ----------------------------------------------------------------- events

    def add_event(self, evt: Event) -> bool:
        """Append an event. Returns False if it was already recorded (dedupe)."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO events(repo_id, number, source, kind, external_id,"
            " payload_json, received_at) VALUES (?,?,?,?,?,?,?)",
            (
                self.repo_id(evt.repo),
                evt.number,
                evt.source,
                evt.kind,
                evt.external_id,
                json.dumps(evt.payload),
                time.time(),
            ),
        )
        return cur.rowcount == 1

    def pending_events(self, limit: int = 100) -> list[Event]:
        rows = self.conn.execute(
            "SELECT e.*, r.full_name FROM events e JOIN repos r ON r.id=e.repo_id"
            " WHERE e.processed_at IS NULL ORDER BY e.id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Event(
                id=r["id"],
                repo=r["full_name"],
                number=r["number"],
                source=r["source"],
                kind=r["kind"],
                external_id=r["external_id"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]

    def mark_processed(self, event_id: int) -> None:
        self.conn.execute("UPDATE events SET processed_at=? WHERE id=?", (time.time(), event_id))

    # ------------------------------------------------------------ issue state

    def load_issue(self, repo: str, number: int) -> IssueState:
        row = self.conn.execute(
            "SELECT state_json FROM issues WHERE repo_id=? AND number=?",
            (self.repo_id(repo), number),
        ).fetchone()
        if row is None:
            return IssueState(repo=repo, number=number)
        return IssueState.model_validate_json(row["state_json"])

    def commit_issue(self, state: IssueState, expected_version: int) -> IssueState:
        """Compare-and-swap on phase_version. Returns the stored state (version bumped)."""
        rid = self.repo_id(state.repo)
        new = state.model_copy(update={"version": expected_version + 1})
        self.conn.execute(
            "INSERT OR IGNORE INTO issues(repo_id, number, state_json, phase, phase_version)"
            " VALUES (?,?,?,'NEW',0)",
            (rid, state.number, IssueState(repo=state.repo, number=state.number).model_dump_json()),
        )
        cur = self.conn.execute(
            "UPDATE issues SET state_json=?, phase=?, phase_version=?"
            " WHERE repo_id=? AND number=? AND phase_version=?",
            (new.model_dump_json(), new.phase.value, new.version, rid, state.number, expected_version),
        )
        if cur.rowcount != 1:
            raise VersionConflict(f"{state.repo}#{state.number} v{expected_version}")
        return new

    @contextmanager
    def issue_lock(self, repo: str, number: int, ttl: float = 300) -> Iterator[None]:
        rid = self.repo_id(repo)
        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO issues(repo_id, number, state_json, phase, phase_version)"
            " VALUES (?,?,?,?,0)",
            (rid, number, IssueState(repo=repo, number=number).model_dump_json(), "NEW"),
        )
        cur = self.conn.execute(
            "UPDATE issues SET locked_until=? WHERE repo_id=? AND number=?"
            " AND (locked_until IS NULL OR locked_until < ?)",
            (now + ttl, rid, number, now),
        )
        if cur.rowcount != 1:
            raise VersionConflict(f"{repo}#{number} is locked")
        try:
            yield
        finally:
            self.conn.execute(
                "UPDATE issues SET locked_until=NULL WHERE repo_id=? AND number=?", (rid, number)
            )

    # ----------------------------------------------------------------- timers

    def add_timer(self, due_at: float, kind: str, ref: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO timers(due_at, kind, ref_json) VALUES (?,?,?)",
            (due_at, kind, json.dumps(ref, sort_keys=True)),
        )
        return int(cur.lastrowid)

    def due_timers(self, now: float | None = None) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM timers WHERE status='pending' AND due_at<=? ORDER BY due_at",
            (now or time.time(),),
        ).fetchall()

    def set_timer_status(self, timer_id: int, status: str) -> None:
        self.conn.execute("UPDATE timers SET status=? WHERE id=?", (status, timer_id))

    def cancel_timers(self, repo: str, number: int, kinds: list[str] | None = None) -> None:
        sql = (
            "UPDATE timers SET status='cancelled' WHERE status='pending'"
            " AND json_extract(ref_json,'$.repo')=? AND json_extract(ref_json,'$.number')=?"
        )
        args: list[Any] = [repo, number]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        self.conn.execute(sql, args)

    # ---------------------------------------------------------------- actions

    def action_status(self, idem_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM actions WHERE idem_key=?", (idem_key,)
        ).fetchone()
        return row["status"] if row else None

    def record_action(
        self,
        idem_key: str,
        repo: str,
        number: int,
        kind: str,
        payload: dict[str, Any],
        status: str,
        error_class: str | None = None,
        github_ref: str | None = None,
    ) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO actions(idem_key, repo_id, number, kind, payload_json, status, attempts,"
            " error_class, github_ref, created_at, executed_at) VALUES (?,?,?,?,?,?,1,?,?,?,?)"
            " ON CONFLICT(idem_key) DO UPDATE SET status=excluded.status,"
            " attempts=actions.attempts+1, error_class=excluded.error_class,"
            " github_ref=COALESCE(excluded.github_ref, actions.github_ref),"
            " executed_at=excluded.executed_at",
            (
                idem_key,
                self.repo_id(repo),
                number,
                kind,
                json.dumps(payload),
                status,
                error_class,
                github_ref,
                now,
                now,
            ),
        )

    # ------------------------------------------------------------------ caches

    def gh_cache_get(self, key: str) -> tuple[str | None, str | None]:
        row = self.conn.execute("SELECT etag, body FROM gh_cache WHERE key=?", (key,)).fetchone()
        return (row["etag"], row["body"]) if row else (None, None)

    def gh_cache_put(self, key: str, etag: str | None, body: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO gh_cache(key, etag, body, fetched_at) VALUES (?,?,?,?)",
            (key, etag, body, time.time()),
        )

    def llm_cache_get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT response_json FROM llm_cache WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def llm_cache_put(
        self, key: str, stage: str, model: str, prompt_version: str, response: dict[str, Any]
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache(key, stage, model, prompt_version, response_json,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (key, stage, model, prompt_version, json.dumps(response), time.time()),
        )

    # ------------------------------------------------------------------- runs

    def start_run(self, repo: str, number: int, kind: str, **fields: Any) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(repo_id, number, kind, started_at, status, agent_version,"
            " prompt_version, model, as_of_sha, event_id, eval_run_id)"
            " VALUES (?,?,?,?, 'running', ?,?,?,?,?,?)",
            (
                self.repo_id(repo),
                number,
                kind,
                time.time(),
                fields.get("agent_version"),
                fields.get("prompt_version"),
                fields.get("model"),
                fields.get("as_of_sha"),
                fields.get("event_id"),
                fields.get("eval_run_id"),
            ),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, **fields: Any) -> None:
        cols = ["status=?", "ended_at=?"] + [f"{k}=?" for k in fields]
        self.conn.execute(
            f"UPDATE runs SET {', '.join(cols)} WHERE id=?",
            (status, time.time(), *fields.values(), run_id),
        )

    def add_tokens(self, run_id: int, in_tok: int, out_tok: int) -> None:
        self.conn.execute(
            "UPDATE runs SET in_tok=in_tok+?, out_tok=out_tok+? WHERE id=?",
            (in_tok, out_tok, run_id),
        )

    # ----------------------------------------------------------------- ledger

    def add_evidence(self, run_id: int | None, ev: Evidence) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO evidence(id, run_id, kind, locator, sha, excerpt, score,"
            " sensitive, meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ev.id,
                run_id,
                ev.kind,
                ev.locator,
                ev.sha,
                ev.excerpt,
                ev.score,
                int(ev.sensitive),
                json.dumps(ev.meta),
            ),
        )

    def get_evidence(self, ev_id: str) -> Evidence | None:
        row = self.conn.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone()
        if row is None:
            return None
        return Evidence(
            id=row["id"],
            kind=row["kind"],
            locator=row["locator"],
            sha=row["sha"],
            excerpt=row["excerpt"] or "",
            score=row["score"],
            sensitive=bool(row["sensitive"]),
            meta=json.loads(row["meta_json"] or "{}"),
        )
