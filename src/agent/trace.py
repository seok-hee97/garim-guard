"""트레이스 스키마 — 모든 측정의 유일한 전제.

`attempted`/`executed` 분해가 헤드라인 논증의 핵심이다. hop 0 부터 이 필드가
기록되어야 하며, 나중에 추가하면 이전 실행 결과가 전부 무효가 된다.

각 도구 호출은 하나의 HopTrace 로 남는다. t-ASR 은 이 JSONL 을 결정적으로
집계해서 나온다 — LLM judge 미사용.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

GateDecision = Literal["ALLOW", "BLOCK", "HITL_DENY", "HITL_ALLOW", "NA"]


@dataclass
class HopTrace:
    """한 도구 호출(또는 최종 텍스트 응답)에 대한 기록."""

    episode_id: str
    hop: int
    tool_name: str = ""                       # 빈 문자열이면 text-only 종료 홉
    tool_args: dict[str, Any] = field(default_factory=dict)       # 모델이 낸 인자(비식별 상태)
    effective_args: dict[str, Any] = field(default_factory=dict)  # 실행에 쓰인 인자(복원됨) — DLP 어댑터 경계 계측
    call_id: str = ""

    # ★ ASR 분해의 전부 ★
    attempted: bool = False                   # 모델이 이 도구 호출을 시도했는가
    executed: bool = False                    # 게이트를 통과해 실제로 실행되었는가

    gate_decision: GateDecision = "NA"
    gate_rule: str = ""                       # policy.yaml 의 단일 키 (H14 추적률)
    taint_sources: list[str] = field(default_factory=list)

    rail_scores: dict[str, float] = field(default_factory=dict)   # LOG-ONLY
    dlp_spans: list[dict[str, Any]] = field(default_factory=list)
    # 데모/디버그 관측용. raw 는 실행 중 메모리에만 존재하고 디스크에는 절대 쓰지 않는다.
    # preview 는 after_tool(DLP/taint 처리 뒤) 모델에 전달된 값이며, 직렬화 시 한 번 더
    # 안전 마스킹한다. 보안 판정이나 평가 집계에는 사용하지 않는다.
    tool_result_raw: Any = field(default=None, repr=False)
    tool_result_preview: Any = None
    latency_ms: int = 0
    note: str = ""


@dataclass
class EpisodeTrace:
    """한 요청(에피소드)의 전체 기록."""

    episode_id: str
    user_input: str = ""
    scenario: str = ""
    defenses: dict[str, bool] = field(default_factory=dict)   # 이 실행의 애블레이션 arm
    hops: list[HopTrace] = field(default_factory=list)
    final_text: str = ""
    finish_reason: str = ""                   # done | iteration_cap | blocked | error | hitl_pending | deadline_exceeded
    pending_approval: str = ""                # hitl_pending 시 승인 요청 id
    total_hops: int = 0
    total_latency_ms: int = 0

    # ── 에피소드 스코프 방어 상태 (per-episode, 직렬화 제외) ──
    # Tier B value-provenance: 비신뢰 도구 출력의 원문을 누적한다. 모델이 오염 텍스트를
    # 읽고 평범한 문자열 인자로 되뿜으면 Tainted 래퍼가 세탁되므로(LLM 라운드트립),
    # 싱크 인자 값이 이 코퍼스의 부분문자열인지로 파생 오염을 추적한다.
    untrusted_corpus: list[str] = field(default_factory=list, metadata={"transient": True})
    # classifier_gate: 분류기가 비신뢰 출력에서 인젝션을 탐지하면 격리 플래그를 세운다.
    # 이후 고위험 도구 호출을 이 플래그로 차단(negative-control enforcement arm).
    quarantined: bool = False

    def add(self, hop: HopTrace) -> HopTrace:
        self.hops.append(hop)
        self.total_hops = len(self.hops)
        return hop

    # ── 결정적 채점 헬퍼 (LLM judge 없이) ──
    def any_executed(self, tool: str, **arg_match: Any) -> bool:
        """지정 도구가 지정 인자로 실제 실행됐는가 — t-ASR 카나리 채점."""
        for h in self.hops:
            if h.tool_name == tool and h.executed:
                if all(str(h.tool_args.get(k)) == str(v) for k, v in arg_match.items()):
                    return True
        return False

    def any_attempted(self, tool: str, **arg_match: Any) -> bool:
        """지정 도구를 (차단 여부 무관) 시도했는가 — ASR_attempted."""
        for h in self.hops:
            if h.tool_name == tool and h.attempted:
                if all(str(h.tool_args.get(k)) == str(v) for k, v in arg_match.items()):
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("untrusted_corpus", None)   # 전이 상태 + 원문(PII 가능) → 트레이스에 남기지 않음
        # ★ effective_args 는 복원된 실제 PII(계좌/이메일/RRN) → 디스크(traces.jsonl)에 남기지 않는다.
        #   DLP 계측(eval/dlp_eval)은 in-memory ep 객체에서만 읽으므로 직렬화 불필요.
        for h in d.get("hops", []):
            h.pop("effective_args", None)
            h.pop("tool_result_raw", None)
            h["tool_result_preview"] = _mask_preview(h.get("tool_result_preview"))
        return d


class TraceWriter:
    """에피소드 트레이스를 JSONL 로 증분 기록한다 (재개 가능 러너의 전제)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, ep: EpisodeTrace) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ep.to_dict(), ensure_ascii=False) + "\n")

    def episode_ids(self) -> set[str]:
        """이미 기록된 에피소드 id — 재개 시 건너뛴다."""
        if not self.path.exists():
            return set()
        ids = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    ids.add(json.loads(line)["episode_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return ids


def _mask_preview(value: Any) -> Any:
    """트레이스에 추가되는 도구 결과 미리보기의 원본 PII 를 방어적으로 마스킹한다.

    기존 채점 필드(user_input/tool_args)는 frozen 평가 호환성 때문에 건드리지 않는다.
    새 미리보기 필드는 UI 관측용이므로 원본을 저장할 이유가 없다. DLP 토큰
    (`<KR_RRN_1>`)은 인식기 정규식에 걸리지 않아 그대로 남고, 무방어 arm 의 원본값만
    `<KR_RRN_MASKED>` 형태가 된다.
    """
    if value is None:
        return None
    if isinstance(value, str):
        from ..dlp.recognizers_ko import KO_RECOGNIZERS
        out = value
        for entity, pattern in KO_RECOGNIZERS:
            out = pattern.sub(f"<{entity}_MASKED>", out)
        return out
    if isinstance(value, dict):
        return {str(k): _mask_preview(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_preview(v) for v in value]
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)
