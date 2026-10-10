"""NVIDIA Nemotron via the OpenAI-compatible API: rate limiting, caching, JSON validation + repair."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from mergemind import tracing
from mergemind.config import Settings
from mergemind.faults import NO_FAULTS, Faults
from mergemind.store.db import Store

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    def __init__(self, error_class: str, message: str):
        super().__init__(f"{error_class}: {message}")
        self.error_class = error_class


def reasoning_body(on: bool) -> dict[str, Any]:
    """Nemotron 3 toggles reasoning per request through the chat template.

    Confirm the exact flag on each model card during the Day-0 smoke test; it is
    isolated here so a change is one line.
    """
    return {"chat_template_kwargs": {"enable_thinking": on}}


class RateLimiter:
    """Sliding-window requests-per-minute limiter (client side)."""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.rpm:
                time.sleep(60 - (now - self.calls[0]) + 0.05)
            self.calls.append(time.monotonic())


@dataclass
class Usage:
    in_tok: int = 0
    out_tok: int = 0
    calls: int = 0
    cached: int = 0
    repairs: int = 0
    by_model: dict[str, list[int]] = field(default_factory=dict)  # model -> [in, out] (for cost)

    def add(self, other: "Usage") -> None:
        for f in ("in_tok", "out_tok", "calls", "cached", "repairs"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        for model, (i, o) in other.by_model.items():
            cur = self.by_model.setdefault(model, [0, 0])
            cur[0] += i
            cur[1] += o


@dataclass
class ChatResult:
    message: dict[str, Any]  # {"role","content","tool_calls"?}
    finish_reason: str | None
    usage: Usage = field(default_factory=Usage)


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON from model output: plain, fenced, or the outermost {...}."""
    text = (text or "").strip()
    # drop a leaked reasoning block if present
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        return json.loads(m.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("no JSON object found", text, 0)


class LLM:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        faults: Faults = NO_FAULTS,
        client: Any = None,
    ):
        self.settings = settings
        self.store = store
        self.faults = faults
        self.limiter = RateLimiter(settings.llm_rpm)
        if client is None:
            from openai import OpenAI

            if not settings.nvidia_api_key:
                raise LLMError("config", "NVIDIA_API_KEY is not set")
            client = OpenAI(base_url=settings.llm_base_url, api_key=settings.nvidia_api_key)
        self.client = tracing.wrap_llm(client)

    # ---------------------------------------------------------------- chat

    def chat(
        self,
        *,
        stage: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        temperature: float | None = None,
        max_tokens: int = 4096,
        prompt_version: str = "v1",
        cache: bool = True,
    ) -> ChatResult:
        key = self._cache_key(stage, model, prompt_version, messages, tools, reasoning)
        if cache and self.store:
            hit = self.store.llm_cache_get(key)
            if hit:
                return ChatResult(hit["message"], hit["finish_reason"], Usage(cached=1))

        resp = self._call_with_retries(
            model=model,
            messages=messages,
            tools=tools,
            reasoning=reasoning,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result = self._to_result(resp, model)

        # truncated or empty → one retry with more room and reasoning off
        if result.finish_reason == "length" or (
            not result.message.get("content") and not result.message.get("tool_calls")
        ):
            log.info("llm %s: %s output, retrying once", stage, result.finish_reason or "empty")
            resp = self._call_with_retries(
                model=model,
                messages=messages,
                tools=tools,
                reasoning=False,
                temperature=temperature,
                max_tokens=max_tokens * 2,
            )
            retry = self._to_result(resp, model)
            retry.usage.add(result.usage)
            result = retry
            if result.finish_reason == "length" or (
                not result.message.get("content") and not result.message.get("tool_calls")
            ):
                raise LLMError("truncated_or_empty", f"stage {stage}")

        if cache and self.store:
            self.store.llm_cache_put(
                key,
                stage,
                model,
                prompt_version,
                {"message": result.message, "finish_reason": result.finish_reason},
            )
        return result

    def structured(
        self,
        schema: type[T],
        *,
        stage: str,
        model: str,
        messages: list[dict[str, Any]],
        reasoning: bool = False,
        prompt_version: str = "v1",
        max_tokens: int = 4096,
    ) -> tuple[T, Usage]:
        """Single-shot JSON output validated by `schema`, with one repair turn."""
        sys_hint = {
            "role": "system",
            "content": "Reply with a single JSON object only, matching this JSON schema:\n"
            + json.dumps(schema.model_json_schema()),
        }
        convo = [sys_hint, *messages]
        usage = Usage()
        last_error = ""
        for attempt in range(2):
            res = self.chat(
                stage=stage,
                model=model,
                messages=convo,
                reasoning=reasoning,
                temperature=0 if not reasoning else None,
                max_tokens=max_tokens,
                prompt_version=prompt_version,
                cache=attempt == 0,
            )
            usage.add(res.usage)
            content = res.message.get("content") or ""
            fault = self.faults.check(f"llm.{stage}")
            if fault == "bad_json":
                content = "{not json"
            try:
                return schema.model_validate(extract_json(content)), usage
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:1500]
                usage.repairs += 1
                # append-only history: show the model its output and what was wrong
                convo = [
                    *convo,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That output was invalid:\n"
                        + last_error
                        + "\nReturn the corrected JSON object only.",
                    },
                ]
        raise LLMError("invalid_output", last_error)

    # ------------------------------------------------------------- helpers

    def _call_with_retries(self, **kw: Any) -> Any:
        from openai import APIConnectionError, APIStatusError

        attempt = 0
        while True:
            attempt += 1
            self.limiter.wait()
            fault = self.faults.check("llm.api")
            try:
                if fault == "429":
                    raise LLMError("rate_limited", "injected")
                if fault == "500":
                    raise LLMError("server", "injected")
                return self._raw_call(**kw)
            except APIStatusError as exc:
                cls = "rate_limited" if exc.status_code == 429 else (
                    "server" if exc.status_code >= 500 else "client"
                )
                err = LLMError(cls, str(exc)[:300])
            except APIConnectionError as exc:
                err = LLMError("network", str(exc)[:300])
            except LLMError as exc:
                err = exc
            if err.error_class in ("rate_limited", "server", "network") and attempt <= 4:
                time.sleep(min(60.0, 2**attempt))
                continue
            raise err

    def _raw_call(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        reasoning: bool,
        temperature: float | None,
        max_tokens: int,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "extra_body": reasoning_body(reasoning),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self.client.chat.completions.create(**kwargs)

    @staticmethod
    def _to_result(resp: Any, model: str = "") -> ChatResult:
        choice = resp.choices[0]
        msg = choice.message
        message: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if getattr(msg, "tool_calls", None):
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        u = getattr(resp, "usage", None)
        in_tok, out_tok = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
        usage = Usage(in_tok=in_tok, out_tok=out_tok, calls=1, by_model={model: [in_tok, out_tok]} if model else {})
        return ChatResult(message, choice.finish_reason, usage)

    @staticmethod
    def _cache_key(stage: str, model: str, pv: str, messages: Any, tools: Any, reasoning: bool) -> str:
        raw = json.dumps([stage, model, pv, messages, tools, reasoning], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
