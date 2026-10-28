"""L2 판정기 예시 — 직접 학습한 Hugging Face 문장 분류 모델(프롬프트 공격 분류기).

LLM 서버 없이 분류기 하나를 thorough 모드의 판정기로 쓴다. 자기 모델의 라벨을 가림의 네 범주
(`NONE` · `INJECTION` · `JAILBREAK` · `SYSTEM_LEAK`)로 옮기는 표만 맞춘다.
필요: `pip install transformers torch`.

    GARIM_ATTACK_MODEL=./my-attack-classifier garim --mode thorough \\
        --l2-backend examples/backends/l2_hf_classifier.py:build check '이전 지시를 무시해'

로컬 LLM(Ollama · llama-server)을 판정기로 쓸 때는 이 파일이 필요 없다 — `--l2-endpoint` ·
`--l2-model` 로 붙인다(docs/INTEGRATION.md).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from garim.detect.l2 import L2_LABELS, L2Judgement, L2ParseError
from garim.models import Origin

# 자기 모델의 라벨 → 가림 범주. 표에 없는 라벨이 나오면 L2ParseError — 판정은 degraded 가 된다.
LABEL_MAP = {
    "BENIGN": "NONE",
    "SAFE": "NONE",
    "INJECTION": "INJECTION",
    "JAILBREAK": "JAILBREAK",
    "SYSTEM_LEAK": "SYSTEM_LEAK",
}


class HfClassifierJudge:
    def __init__(
        self,
        classify: Callable[..., list[dict[str, Any]]],
        model: str,
        label_map: dict[str, str] | None = None,
    ) -> None:
        self.name = f"hf-classifier:{model}"
        self._model = model
        self._classify = classify
        self._label_map = dict(LABEL_MAP if label_map is None else label_map)
        unknown = set(self._label_map.values()) - set(L2_LABELS)
        if unknown:
            raise ValueError(f"LABEL_MAP targets must be one of {L2_LABELS}: {sorted(unknown)}")
        self._lock = threading.Lock()  # 파이프라인은 스레드 안전을 보장하지 않는다

    def judge(self, text: str, *, origin: Origin) -> L2Judgement:
        del origin
        with self._lock:
            top = self._classify(text, truncation=True)[0]
        label = self._label_map.get(str(top["label"]))
        if label is None:
            raise L2ParseError(f"classifier returned an unmapped label: {top['label']!r}")
        # 공격 판정의 신뢰도는 정책 임계 `thresholds.l0`(기본 0.5)와 비교된다.
        return L2Judgement(label, float(top["score"]), self._model)


def build() -> HfClassifierJudge:
    model = os.environ.get("GARIM_ATTACK_MODEL")
    if not model:
        raise ValueError("set GARIM_ATTACK_MODEL to a local text-classification model directory")
    from transformers import pipeline

    return HfClassifierJudge(pipeline("text-classification", model=model), model)
