"""PresidioVault — Presidio 한국어 인식기 기반 가역 볼트 (RegexVault 대안).

동일 `Vault` 프로토콜. Presidio 의 PatternRecognizer 클래스를 **직접** 사용해
AnalyzerEngine/spacy 모델 의존을 피한다(pattern recognizer 는 nlp_artifacts 불필요).

★ 검증된 Presidio 한국어 결함 2건을 여기서 패치해 그 효과를 실측한다:
  결함1 — KrPassportRecognizer.supported_language="kr" → language="ko" 에서 미로드.
          패치: supported_language="ko" 로 재인스턴스.
  결함2 — KrRrnRecognizer CONTEXT 영어 전용 → "주민등록번호" 문맥 가점 없음.
          패치: 한글 문맥어 주입.
"""

from __future__ import annotations

import re
from typing import Any

_HANGUL_RRN_CONTEXT = ["주민등록번호", "주민번호", "등록번호"]


def _load_recognizers(patch_defects: bool = True) -> list[Any]:
    """한국어 Presidio 인식기 로드 (+ 결함 패치)."""
    import presidio_analyzer.predefined_recognizers as pr

    recs = []
    for name in dir(pr):
        if not (name.startswith("Kr") and name.endswith("Recognizer")):
            continue
        cls = getattr(pr, name)
        try:
            inst = cls()
        except Exception:  # noqa: BLE001
            continue
        if patch_defects:
            # 결함1: 여권 인식기 언어코드 kr→ko
            if getattr(inst, "supported_language", None) == "kr":
                try:
                    inst = cls(supported_language="ko")
                except Exception:  # noqa: BLE001
                    inst.supported_language = "ko"
            # 결함2: RRN 에 한글 문맥어 주입
            if "Rrn" in name:
                ctx = list(getattr(inst, "context", []) or [])
                inst.context = ctx + _HANGUL_RRN_CONTEXT
        recs.append(inst)
    return recs


class PresidioVault:
    """Presidio 인식기 기반. RegexVault 와 동일 인터페이스."""

    def __init__(self, patch_defects: bool = True):
        self._recs = _load_recognizers(patch_defects)
        self._v2t: dict[str, str] = {}
        self._t2v: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._tok_re = re.compile(r"<([A-Z_]+)_(\d+)>")

    def _token_for(self, entity: str, value: str) -> str:
        if value in self._v2t:
            return self._v2t[value]
        self._counters[entity] = self._counters.get(entity, 0) + 1
        tok = f"<{entity}_{self._counters[entity]}>"
        self._v2t[value] = tok
        self._t2v[tok] = value
        return tok

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        out = []
        for rec in self._recs:
            try:
                results = rec.analyze(text=text, entities=None, nlp_artifacts=None)
            except Exception:  # noqa: BLE001
                continue
            for r in results or []:
                out.append((r.start, r.end, getattr(r, "entity_type", "PII")))
        return out

    def redact(self, text: str) -> str:
        if not text:
            return text
        spans = sorted(self._spans(text), key=lambda s: (s[0], -(s[1] - s[0])))
        result, last = [], 0
        for start, end, entity in spans:
            if start < last:
                continue
            result.append(text[last:start])
            result.append(self._token_for(entity, text[start:end]))
            last = end
        result.append(text[last:])
        return "".join(result)

    def rehydrate(self, text: str) -> str:
        return self.restore(text)

    def restore(self, text: str) -> str:
        if not text:
            return text
        return self._tok_re.sub(lambda m: self._t2v.get(m.group(0), m.group(0)), text)

    def contains_raw(self, text: str) -> list[str]:
        return [v for v in self._v2t if v and v in (text or "")]
