"""Environment settings and the per-repo policy file (.github/mergemind.yml)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_serializer, field_validator

Autonomy = Literal["observe", "suggest", "invite", "merge"]

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str | int | float) -> float:
    """'48h' -> 172800.0 seconds. Plain numbers are seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION.match(value.strip())
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2)]


def parse_percent(value: str | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(value.strip().rstrip("%")) / 100


class Settings(BaseModel):
    # credentials never appear in repr() or serialized output (logs, traces, errors)
    nvidia_api_key: str | None = Field(default=None, repr=False)
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_model_investigator: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_model_fast: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    llm_rpm: int = 30
    neatlogs_api_key: str | None = Field(default=None, repr=False)
    github_token: str | None = Field(default=None, repr=False)
    gh_app_id: str | None = None
    gh_app_key: str | None = Field(default=None, repr=False)
    db_path: Path = Path("mergemind.db")
    work_dir: Path = Path(".mergemind")
    autonomy_override: Autonomy | None = None
    faults: str = ""
    poll_interval_s: float = 15.0
    eval_run_id: str | None = None  # tags traces and runs, e.g. v1

    @field_serializer("nvidia_api_key", "neatlogs_api_key", "github_token", "gh_app_key")
    def _hide_secret(self, value: str | None) -> str | None:
        return "[REDACTED]" if value else None

    @classmethod
    def from_env(cls) -> "Settings":
        # .env.local wins over .env; neither overrides variables already set in the shell
        load_dotenv(".env.local")
        load_dotenv(".env")
        env = os.environ.get
        return cls(
            nvidia_api_key=env("NVIDIA_API_KEY"),
            llm_base_url=env("LLM_BASE_URL", cls.model_fields["llm_base_url"].default),
            llm_model_investigator=env(
                "LLM_MODEL_INVESTIGATOR", cls.model_fields["llm_model_investigator"].default
            ),
            llm_model_fast=env("LLM_MODEL_FAST", cls.model_fields["llm_model_fast"].default),
            llm_rpm=int(env("LLM_RPM", "30")),
            neatlogs_api_key=env("NEATLOGS_API_KEY"),
            github_token=env("GITHUB_TOKEN") or env("GH_TOKEN"),
            gh_app_id=env("GH_APP_ID"),
            gh_app_key=env("GH_APP_KEY"),
            db_path=Path(env("MERGEMIND_DB", "mergemind.db")),
            work_dir=Path(env("MERGEMIND_WORK_DIR", ".mergemind")),
            autonomy_override=env("MERGEMIND_AUTONOMY") or None,
            faults=env("MERGEMIND_FAULTS", ""),
            poll_interval_s=float(env("MERGEMIND_POLL_S", "15")),
            eval_run_id=env("MERGEMIND_EVAL_RUN") or None,
        )


# ---------------------------------------------------------------- policy file


class AccessPolicy(BaseModel):
    mode: Literal["collaborator", "fork"] = "collaborator"
    permission: Literal["push", "triage"] = "push"
    ttl: float = parse_duration("14d")
    checkin_at: float = 0.5
    min_account_age: float = parse_duration("30d")
    max_active_leases_per_person: int = 1

    @field_validator("ttl", "min_account_age", mode="before")
    @classmethod
    def _duration(cls, v: str | float) -> float:
        return parse_duration(v)

    @field_validator("checkin_at", mode="before")
    @classmethod
    def _percent(cls, v: str | float) -> float:
        return parse_percent(v)


class PRPolicy(BaseModel):
    require_regression_test: bool = True
    test_commands: list[str] = Field(default_factory=list)
    allow_out_of_scope: Literal["flag", "block"] = "flag"
    merge_method: Literal["squash", "merge", "rebase"] = "squash"
    post_merge_watch: float = parse_duration("30m")
    ci_timeout: float = parse_duration("30m")  # how long to wait for mergemind-verify results

    @field_validator("post_merge_watch", "ci_timeout", mode="before")
    @classmethod
    def _duration(cls, v: str | float) -> float:
        return parse_duration(v)


class GuardPolicy(BaseModel):
    sensitive_paths: list[str] = Field(
        default_factory=lambda: [
            ".env*",
            "**/.env*",
            "**/*.pem",
            "**/*.key",
            "**/secrets/**",
            "deploy/**",
            "infra/**",
        ]
    )
    maintainer_channel: str | None = None


class DemoPolicy(BaseModel):
    stand_ins: dict[str, str] = Field(default_factory=dict)
    time_scale: float = 1.0


class Policy(BaseModel):
    autonomy: Autonomy = "suggest"
    maintainers: list[str] = Field(default_factory=list)
    escalate_to: list[str] = Field(default_factory=list)
    access: AccessPolicy = Field(default_factory=AccessPolicy)
    pr: PRPolicy = Field(default_factory=PRPolicy)
    guard: GuardPolicy = Field(default_factory=GuardPolicy)
    demo: DemoPolicy = Field(default_factory=DemoPolicy)

    @classmethod
    def from_yaml(cls, text: str | None) -> "Policy":
        data = yaml.safe_load(text) if text else None
        return cls.model_validate(data or {})

    def effective_autonomy(self, settings: Settings) -> Autonomy:
        """The env kill switch can only lower autonomy, never raise it."""
        order = ["observe", "suggest", "invite", "merge"]
        if settings.autonomy_override is None:
            return self.autonomy
        return order[min(order.index(self.autonomy), order.index(settings.autonomy_override))]  # type: ignore[return-value]

    def scaled(self, seconds: float) -> float:
        return seconds * self.demo.time_scale
