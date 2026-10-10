"""neatlogs setup (buildplan-v2 §10). Tracing is optional: without NEATLOGS_API_KEY every
helper is a no-op, so tracing can never take the agent down.

- init(tags=[...])        every trace carries the tags (e.g. eval:v1) for before/after views
- span(kind, name)        a span per step (WORKFLOW / AGENT / TOOL / RETRIEVER / GUARDRAIL / CHAIN)
- attrs(**kv)             custom `neatlogs.*` attributes on the current span
- session(repo, number)   groups every trace of one issue into one neatlogs session
- log(msg, **data)        a timestamped step inside the current span
"""

from __future__ import annotations

import contextlib
import functools
import logging
from typing import Any, Callable, Iterator, TypeVar

log_ = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

_enabled = False
_tags: list[str] = []


def init(api_key: str | None, workflow_name: str = "mergemind", tags: list[str] | None = None) -> bool:
    global _enabled, _tags
    if not api_key:
        return False
    try:
        import neatlogs

        _tags = list(tags or [])
        neatlogs.init(api_key=api_key, workflow_name=workflow_name, tags=_tags or None)
        _enabled = True
    except Exception as exc:  # tracing must never take the agent down
        log_.warning("neatlogs init failed: %s", exc)
    return _enabled


def enabled() -> bool:
    return _enabled


def tags() -> list[str]:
    return list(_tags)


def wrap_llm(client: Any) -> Any:
    if not _enabled:
        return client
    import neatlogs

    return neatlogs.wrap(client)


def span(kind: str, name: str | None = None) -> Callable[[F], F]:
    """@span("TOOL") — a neatlogs span when tracing is on, otherwise a passthrough.

    Decided at call time so modules can be imported before init().
    """

    def deco(fn: F) -> F:
        traced: dict[str, Callable[..., Any]] = {}

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _enabled:
                return fn(*args, **kwargs)
            if "fn" not in traced:
                import neatlogs

                traced["fn"] = neatlogs.span(kind=kind, name=name or fn.__name__)(fn)
            return traced["fn"](*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return deco


def attrs(**values: Any) -> None:
    """Set `neatlogs.<key>` attributes on the current span. Never logs secrets: callers pass
    ids, counts, phases and decisions only."""
    if not _enabled:
        return
    try:
        from opentelemetry import trace

        current = trace.get_current_span()
        for k, v in values.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                v = [str(x) for x in v][:50]
            elif not isinstance(v, (str, bool, int, float)):
                v = str(v)
            current.set_attribute(f"neatlogs.{k}", v)
    except Exception as exc:
        log_.debug("attrs failed: %s", exc)


@contextlib.contextmanager
def session(repo: str, number: int) -> Iterator[None]:
    """All traces for one issue share a neatlogs session id."""
    if not _enabled:
        yield
        return
    import neatlogs

    with neatlogs.identify(session_id=f"{repo}#{number}"):
        yield


def log(message: str, **data: Any) -> None:
    if not _enabled:
        return
    with contextlib.suppress(Exception):
        import neatlogs

        neatlogs.log(message, **data)


def flush() -> None:
    if _enabled:
        with contextlib.suppress(Exception):
            import neatlogs

            neatlogs.flush()


def delivery() -> dict[str, Any]:
    if not _enabled:
        return {}
    import neatlogs

    return neatlogs.get_delivery_diagnostics()
