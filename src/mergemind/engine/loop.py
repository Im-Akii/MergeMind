"""tick(): ingest → fire due timers → process pending events. `run` repeats it."""

from __future__ import annotations

import json
import logging
import time

from mergemind.engine.effects import Context, run_effects
from mergemind.engine.states import transition
from mergemind.github.ingest import ingest, ingest_pulls, poll_invitations
from mergemind.models import Effect, Event
from mergemind.store.db import VersionConflict
from mergemind import tracing
from mergemind.tracing import flush

log = logging.getLogger(__name__)


def fire_due_timers(ctx: Context, now: float | None = None) -> int:
    fired = 0
    for t in ctx.store.due_timers(now):
        ref = json.loads(t["ref_json"])
        if t["kind"] == "retry_effect":
            state = ctx.store.load_issue(ref["repo"], ref["number"])
            follow = run_effects(ctx, state, [Effect.model_validate(ref["effect"])])
            for evt in follow:
                ctx.store.add_event(evt)
        else:
            ctx.store.add_event(Event(
                repo=ref["repo"], number=ref["number"], source="timer", kind="timer",
                external_id=f"timer:{t['id']}",
                payload={**{k: v for k, v in ref.items() if k not in ("repo", "number")}, "timer_kind": t["kind"]},
            ))
        ctx.store.set_timer_status(t["id"], "fired")
        fired += 1
    return fired


def process_event(ctx: Context, evt: Event, now: float | None = None) -> None:
    with tracing.session(evt.repo, evt.number):
        _process_event(ctx, evt, now)


@tracing.span("WORKFLOW", "issue_event")
def _process_event(ctx: Context, evt: Event, now: float | None = None) -> None:
    now = now or time.time()
    with ctx.store.issue_lock(evt.repo, evt.number):
        state = ctx.store.load_issue(evt.repo, evt.number)
        new_state, effects = transition(state, evt, ctx.policy(evt.repo), now, ctx.autonomy(evt.repo))
        if new_state == state and not effects:
            ctx.store.mark_processed(evt.id)
            return
        log.info("[%s#%d] %s: %s -> %s (%d effects)", evt.repo, evt.number, evt.kind,
                 state.phase.value, new_state.phase.value, len(effects))
        tracing.attrs(repo=evt.repo, issue=evt.number, trigger=evt.kind, source=evt.source,
                      phase_from=state.phase.value, phase_to=new_state.phase.value,
                      effects=[e.kind for e in effects], eval_run_id=ctx.settings.eval_run_id)
        committed = ctx.store.commit_issue(new_state, expected_version=state.version)
        follow_ups = run_effects(ctx, committed, effects)
        for f in follow_ups:
            ctx.store.add_event(f)
        ctx.store.mark_processed(evt.id)


def tick(ctx: Context, repos: list[str], max_rounds: int = 5, now: float | None = None) -> dict[str, int]:
    """`now` overrides the clock (tests, demo time travel)."""
    stats = {"ingested": 0, "timers": 0, "processed": 0}
    for repo in repos:
        stats["ingested"] += ingest(ctx.gh, ctx.store, repo)
        stats["ingested"] += ingest_pulls(ctx.gh, ctx.store, repo)
    stats["ingested"] += poll_invitations(ctx.gh, ctx.store)
    stats["timers"] = fire_due_timers(ctx, now)
    # follow-up events (e.g. triage_done) are processed in the same tick
    for _ in range(max_rounds):
        pending = ctx.store.pending_events()
        if not pending:
            break
        for evt in pending:
            try:
                process_event(ctx, evt, now)
                stats["processed"] += 1
            except VersionConflict as e:
                log.info("skip %s: %s (retry next tick)", evt.external_id, e)
    flush()
    return stats


def run_forever(ctx: Context, repos: list[str]) -> None:
    while True:
        started = time.monotonic()
        try:
            stats = tick(ctx, repos)
            if any(stats.values()):
                log.info("tick %s", stats)
        except Exception:
            log.exception("tick failed")
        time.sleep(max(0.0, ctx.settings.poll_interval_s - (time.monotonic() - started)))
