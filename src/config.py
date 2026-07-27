"""config/policy.yaml 로더.

코드 어디에서도 임계값·주소·티어를 하드코딩하지 않는다. 전부 여기를 거친다.
`policy.yaml` 의 tools: 섹션이 곧 provider 에 넘길 ToolSpec 의 원천이다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .providers.base import GenConfig, ToolSpec

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "policy.yaml"

# policy.yaml 의 간이 인자 타입 → JSON Schema 타입
_TYPE_MAP = {
    "str": "string", "int": "integer", "email": "string",
    "kr_rrn": "string", "kr_phone": "string", "kr_account": "string",
}


@lru_cache(maxsize=4)
def load_policy(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_POLICY
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2:
        raise ValueError(f"policy schema_version 2 를 기대했으나 {data.get('schema_version')!r}")
    _validate_rails_log_only(data)
    return data


def _validate_rails_log_only(data: dict[str, Any]) -> None:
    """분류기 레일은 mode=log_only 만 허용된다. 스키마가 차단을 표현할 수 없다.

    이 검사는 규약이 아니라 로더 차원의 강제다 — 'benign' 판정이 결정적 게이트를
    무효화하는 경로가 애초에 만들어지지 못하게 한다.
    """
    for name, cfg in (data.get("classifiers") or {}).items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        mode = cfg.get("mode")
        if mode != "log_only":
            raise ValueError(
                f"classifiers.{name}.mode 는 log_only 여야 한다 (받음: {mode!r}). "
                "분류기는 강제 수단이 될 수 없다 — 차단은 Tier A 게이트만 한다."
            )


def tool_specs(data: dict[str, Any]) -> list[ToolSpec]:
    """policy.yaml tools: → provider 에 넘길 함수 선언 목록."""
    specs = []
    for name, spec in (data.get("tools") or {}).items():
        props, required = {}, []
        for arg, meta in (spec.get("args") or {}).items():
            props[arg] = {"type": _TYPE_MAP.get(meta.get("type", "str"), "string")}
            if meta.get("required"):
                required.append(arg)
        specs.append(ToolSpec(
            name=name,
            description=f"[{spec.get('risk_tier','?')}] {name}",
            parameters={"type": "object", "properties": props, "required": required},
        ))
    return specs


def gen_config(data: dict[str, Any], *, model_key: str = "model") -> GenConfig:
    """활성 provider 에 대한 GenConfig 를 만든다.

    model_key='quarantine_model' 로 부르면 Tier C 격리 Q-LLM 설정을 얻는다.
    """
    active = data["provider"]["active"]
    pcfg = data["provider"].get(active, {})
    sys_file = data.get("agent", {}).get("system_instruction_file")
    sys_text = ""
    if sys_file:
        p = ROOT / sys_file
        if p.exists():
            sys_text = p.read_text(encoding="utf-8")
        else:   # 선언됐으나 부재 → 조용히 빈 프롬프트로 넘기지 말고 경고(무경고 소실 방지)
            import warnings
            warnings.warn(
                f"system_instruction_file '{sys_file}' 부재 → 빈 시스템 프롬프트로 진행", stacklevel=2)
    return GenConfig(
        provider=active,
        model=pcfg.get(model_key) or pcfg.get("model", ""),
        system_instruction=sys_text,
        tools=tool_specs(data),
        safety_threshold=pcfg.get("safety_threshold", "BLOCK_MEDIUM_AND_ABOVE"),
        automatic_function_calling=False,  # 항상 False — before_tool 게이트 보호
        temperature=pcfg.get("temperature", 0.2),
        max_output_tokens=pcfg.get("max_output_tokens", 1024),
    )
