"""PIIVault — 요청 스코프 가역 PII 토큰화. L3 DLP 의 심장.

수명 규율 (프로젝트에서 반복 강조):
  요청당 **정확히 1개** 인스턴스가 루프 전체를 관통한다.
  ❌ fresh-per-call  → 홉마다 새 매핑 → `<KR_RRN_1>` 이 홉마다 다른 사람(coreference 붕괴)
  ❌ global singleton → 요청 간 매핑 공유 → 동시 요청 PII 누출
  in-memory, 완료 시 폐기, **매핑 로깅 절대 금지(PIPA)**.

양방향:
  redact(text)     value→token 비식별 (모델 호출 전)
  rehydrate(text)  token→value 복원 (내부 신뢰 도구 호출 전)
  restore(text)    token→value 복원 (최종 응답)

정본 구현은 RegexVault(의존성 0). Presidio 기반은 vault_presidio.py(선택, 결함 실험용).
동일 `Vault` 프로토콜이라 스왑 가능.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from .recognizers_ko import KO_RECOGNIZERS


@runtime_checkable
class Vault(Protocol):
    def redact(self, text: str) -> str: ...
    def rehydrate(self, text: str) -> str: ...
    def restore(self, text: str) -> str: ...


class RegexVault:
    """요청 스코프 가역 토큰화 (한국어 인식기 기반). 의존성 0.

    같은 원본값 → 같은 토큰(누적 매핑)이라 홉 간 coreference 가 유지된다.
    """

    def __init__(self) -> None:
        # value -> token, token -> value (요청 스코프)
        self._v2t: dict[str, str] = {}
        self._t2v: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        # 복원용 토큰 패턴: <ENTITY_n>
        self._tok_re = re.compile(r"<([A-Z_]+)_(\d+)>")

    def _token_for(self, entity: str, value: str) -> str:
        if value in self._v2t:
            return self._v2t[value]
        self._counters[entity] = self._counters.get(entity, 0) + 1
        tok = f"<{entity}_{self._counters[entity]}>"
        self._v2t[value] = tok
        self._t2v[tok] = value
        return tok

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        # 긴 매칭 우선(겹침 방지). 각 인식기가 (entity, regex) 제공.
        spans: list[tuple[int, int, str, str]] = []
        for entity, pattern in KO_RECOGNIZERS:
            for m in pattern.finditer(out):
                spans.append((m.start(), m.end(), entity, m.group(0)))
        # 겹치는 스팬은 더 긴 것 우선
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        result, last = [], 0
        occupied_end = 0
        for start, end, entity, val in spans:
            if start < max(last, occupied_end):
                continue  # 겹침 스킵
            result.append(out[last:start])
            result.append(self._token_for(entity, val))
            last = end
            occupied_end = end
        result.append(out[last:])
        return "".join(result)

    def rehydrate(self, text: str) -> str:
        """토큰을 원본으로 복원 (내부 신뢰 도구 전용)."""
        return self.restore(text)

    def restore(self, text: str) -> str:
        if not text:
            return text
        # 정확 매칭만 복원한다. LLM 이 토큰을 변형(<email_1>·< EMAIL_1 >)하면 복원되지
        # 않는다(알려진 한계 — 관용 매칭 미구현).
        def repl(m: re.Match) -> str:
            tok = m.group(0)
            return self._t2v.get(tok, tok)
        return self._tok_re.sub(repl, text)

    # ── 계측 (평가용) ──
    def mapping_count(self) -> int:
        return len(self._v2t)

    def contains_raw(self, text: str) -> list[str]:
        """text 에 원본 PII 값이 그대로 노출됐는지 (싱크별 유출률 측정).

        ⚠️ **범위 한계(정직 표기):** 이 검사는 볼트가 **이미 매핑한** 값(입력에서 탐지·토큰화된
        PII)만 잡는다. 모델이 **새로 생성**했거나 탐지기(RegexVault)가 **놓친** PII 는 여기서
        잡히지 않는다. 즉 'vault ON 유출 0%'는 *탐지·토큰화된 PII 에 한정*한 주장이며,
        탐지 재현율(recognizer recall) 밖의 PII 는 별개 축(R6/Presidio 평가)에서 다룬다."""
        return [v for v in self._v2t if v and v in (text or "")]


def new_vault() -> Vault:
    """요청당 1개. 이 함수를 요청 시작에서 호출하고 루프 전체에 전달한다."""
    return RegexVault()
