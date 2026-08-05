"""Validated policy schema. Invalid custom policy files fail closed at startup."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["allow", "mask", "warn", "block"]


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Mode = "mask"


class RuleSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rrn_anchor: bool = True

    @field_validator("rrn_anchor", mode="before")
    @classmethod
    def parse_switch(cls, value: object) -> object:
        if isinstance(value, str) and value.lower() in {"on", "off"}:
            return value.lower() == "on"
        return value


class ContentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pii_threshold_delta: float = 0.0
    secret_threshold_delta: float = 0.0
    attack_threshold_delta: float = 0.0
    identifier_allowlist: list[str] = Field(default_factory=list)
    key_exclude: bool = False
    suppress: list[str] = Field(default_factory=list)


def _default_content_policies() -> dict[str, ContentPolicy]:
    return {
        "code": ContentPolicy(
            pii_threshold_delta=0.15,
            secret_threshold_delta=-0.10,
            attack_threshold_delta=0.10,
            identifier_allowlist=[
                "user_name",
                "phone_number",
                "ssn",
                "email",
                "address",
                "birth_date",
                "card_no",
                "account",
            ],
        ),
        "structured": ContentPolicy(key_exclude=True, secret_threshold_delta=-0.10),
        "log": ContentPolicy(suppress=["L-CM-04"], attack_threshold_delta=0.10),
    }


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    l1_pii: float = 0.5
    l1_atk: float = 0.5
    l0: float = 0.5
    l2_escalate: tuple[float, float] = (0.4, 0.6)
    # ★ 보조 헤드 임계 — 초판은 pipeline.py 에 0.5 로 하드코딩돼 있어
    #   l1_atk 를 조정해도 이 경로가 움직이지 않았다. §6.2 6단계의
    #   캘리브레이션 대상이므로 정책에서 와야 한다.
    romanized_evasion: float = 0.5
    honorific_social_engineering: float = 0.5


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_input_chars: int = Field(100_000, ge=1)
    max_segments: int = Field(64, ge=1)
    chunk_size: int = Field(512, ge=32)
    chunk_overlap: int = Field(64, ge=0)
    # ★ §3.6 "L1 지연 예산 초과 → 타임아웃 후 L0 결과 채택, degraded".
    #   초판은 동기 호출 + except 뿐이라 1.5초 걸리는 백엔드에 1.51초 블로킹하고
    #   degraded=False 를 냈다. §7.1.3 의 세그먼트당 L1 예산이 160ms 다.
    l1_timeout_ms: int = Field(1_000, ge=1)


class CustomPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = "L-CT-03"
    name: str
    regex: str
    mode: Mode = "mask"


class OriginPolicy(BaseModel):
    """origin 축 정책 — DESIGN §2.5b.

    ★ `origin` 은 인증된 라벨이 아니라 **호출자가 알려주는 자기신고 필드**다(§3.2.1).
    그래서 신뢰 게이트를 기본값으로 켜면 `origin="system"` 이라 선언하는 것만으로
    공격 탐지가 통째로 꺼진다. 초판이 정확히 그 상태였다.

    `trust_declared` 가 True 일 때만 system 강등이 적용된다. 프록시가 OpenAI
    `messages[].role` 을 실제로 파싱한 경우에만 켠다 — 라이브러리 모드에서
    호출자가 문자열에 붙여 준 라벨은 신뢰하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")
    trust_declared: bool = False
    system_attack_verdict_cap: Mode = "warn"
    system_suppress_codes: list[str] = Field(default_factory=lambda: ["A-INJ-01"])
    system_secret_threshold_delta: float = -0.10


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    defaults: Defaults = Field(default_factory=Defaults)
    rules: RuleSwitches = Field(default_factory=RuleSwitches)
    codes: dict[str, Mode] = Field(default_factory=dict)
    content_type_policy: dict[str, ContentPolicy] = Field(default_factory=_default_content_policies)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    limits: Limits = Field(default_factory=Limits)
    origin_policy: OriginPolicy = Field(default_factory=OriginPolicy)
    custom_patterns: list[CustomPattern] = Field(default_factory=list)


def load_policy(path: str | Path | None = None) -> PolicyConfig:
    if path is None:
        return PolicyConfig()
    policy_path = Path(path)
    try:
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load policy {policy_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"policy {policy_path} must contain a YAML mapping")
    return PolicyConfig.model_validate(payload)
