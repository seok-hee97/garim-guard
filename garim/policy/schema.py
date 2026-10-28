"""Validated policy schema. Invalid custom policy files fail closed at startup."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal["allow", "mask", "warn", "block"]


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Mode = "mask"


class RuleSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rrn_anchor: bool = True
    # ★ §2.5 위치 가중치 임계. 실측은 표본 10건이고 그 절에서 스스로
    #   "실제 코퍼스로 재조정해야 한다"고 적었으므로 코드에 박지 않고 정책으로 뺀다.
    marker_score_threshold: int = Field(3, ge=1, le=8)

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
    # ★ §3.6 "청크 수 상한 초과 → warn + degraded" 의 상한. 이 필드가 없어서
    #   그 행이 구현되지 않았다 — `max_segments` 가드는 `split_text` 결과만 세고
    #   `chunk_text` 결과를 세지 않았다. 10만자(=max_input_chars) 입력에 L1 `infer`
    #   가 447회 불리고도 `degraded=False` 로 보고됐다. §7.1.3 의 세그먼트당 L1
    #   예산 160ms 를 곱하면 단일 호출이 약 71초다.
    #
    #   ⚠️ 상한은 **L1 에만** 건다. 스캔 자체를 멈추면 안 된다 — 그러면
    #   `chunk.py` 가 닫은 fail-open(뒷부분이 조용히 검사되지 않는 것)이 24k자
    #   이상 입력에서 그대로 되살아난다. `tests/test_segment.py` 의 꼬리 테스트가
    #   실제로 그것을 잡았다. L0 는 결정적이고 싸므로 전량 돈다.
    #
    #   ⚠️ 128 은 실측이 아니다 — §7.1.3 지연 수치 전체가 W1 첫날 실측 대상이고
    #      이 값도 거기서 다시 잡는다. 지금은 "무한이 아니다" 를 보장할 뿐이다.
    max_l1_chunks: int = Field(128, ge=1)
    # L2(thorough) 판정 호출 상한. 생성형 판정은 호출당 초 단위라 L1 보다 훨씬 작게 둔다.
    # 넘치면 L1 과 같은 균등 표본 + warn + degraded(`l2_chunk_cap`). 실측값이 아니다.
    max_l2_chunks: int = Field(32, ge=1)
    # ★ §3.6 "L1 지연 예산 초과 → 타임아웃 후 L0 결과 채택, degraded".
    #   초판은 동기 호출 + except 뿐이라 1.5초 걸리는 백엔드에 1.51초 블로킹하고
    #   degraded=False 를 냈다. §7.1.3 의 세그먼트당 L1 예산이 160ms 다.
    l1_timeout_ms: int = Field(1_000, ge=1)

    @model_validator(mode="after")
    def validate_chunk_window(self) -> Limits:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class CustomPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = "L-CT-03"
    name: str
    regex: str
    mode: Mode = "mask"

    @field_validator("regex")
    @classmethod
    def compile_regex(cls, value: str) -> str:
        # ★ 검증하지 않으면 `re.error` 가 정책 로드가 아니라 엔진 생성에서 터졌다 — CLI 는
        #   트레이스백과 종료 코드 1(= warn)을 냈고, CI 게이트는 그것을 경고로 읽었다.
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        # ★ 빈 문자열에 맞는 패턴(`(?:EMP-)?\d{0,6}`)은 길이 0 탐지를 만들어 모든 검사가
        #   마스킹 단계에서 예외로 멈췄다(전수 검토 2026-10-28).
        if compiled.search("") is not None:
            raise ValueError("custom pattern must not match an empty string")
        return value


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


class AttachmentPolicy(BaseModel):
    """프록시가 받은 첨부(이미지 · 오디오 · 파일)를 어떻게 다룰지.

    - `unmaskable` — 문서 첨부(PDF · DOCX · XLSX · PPTX · HWPX)에서 꺼낸 글에 가려야 할 값이
      나왔을 때.
      문서 안은 가릴 수 없으므로 막거나(`block`) 경고하고 그대로 보낸다(`warn`).
    - `unreadable` — 문서인데 읽지 못했을 때(너무 큼 · 깨짐 · 암호 · 시간 초과 · `[docs]` 없이 받은
      PDF). 기본은 막는다 — 일부러 크게 · 깨지게 만든 문서가 검사를 비껴가지 못하게(재검토).
    - `uninspected` — 글을 꺼낼 수 없는 첨부(이미지 · 오디오 · 레거시 HWP/DOC · 글이 없는 문서 ·
      `file_id` 만 있는 파일 · 원격 이미지 주소). `allow` 는 그대로 보내고, `warn` 은 경고와 함께
      보내고, `block` 은 막는다.
    """

    model_config = ConfigDict(extra="forbid")
    unmaskable: Literal["block", "warn"] = "block"
    unreadable: Literal["allow", "warn", "block"] = "block"
    uninspected: Literal["allow", "warn", "block"] = "allow"


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
    attachments: AttachmentPolicy = Field(default_factory=AttachmentPolicy)


# 패키지에 들어 있는 내장 프로필 — pip 설치에는 저장소의 `config/` 가 없다.
BUILTIN_PROFILES = frozenset({"strict"})


def load_policy(path: str | Path | None = None) -> PolicyConfig:
    """정책 파일을 읽는다. `"strict"` 처럼 내장 프로필 이름도 받는다.

    ★ 처음엔 같은 이름의 파일이 있으면 파일이 이겼다. 저장소에 `strict` 라는 파일을 하나 넣으면
      `--policy strict` 로 도는 CI 게이트가 조용히 그 파일(전부 allow)로 바뀌었다(전수 검토).
      내장 이름은 늘 내장 프로필이다. 그 이름의 파일을 쓰려면 `./strict` 처럼 경로로 준다.
    """
    if path is None:
        return PolicyConfig()
    policy_path = Path(path)
    try:
        if str(path) in BUILTIN_PROFILES:
            source = files("garim.policy").joinpath("profiles", f"{path}.yaml")
            text = source.read_text(encoding="utf-8")
        else:
            text = policy_path.read_text(encoding="utf-8")
        payload = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load policy {policy_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"policy {policy_path} must contain a YAML mapping")
    return PolicyConfig.model_validate(payload)
