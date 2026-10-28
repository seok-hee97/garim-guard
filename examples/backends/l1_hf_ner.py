"""L1 백엔드 예시 2 — 직접 학습한 Hugging Face 토큰 분류(NER) 모델.

가림 형식(4헤드 ONNX)으로 내보내지 않은 모델도 이 래퍼로 붙는다. 자기 모델의 라벨을 가림 라벨로
옮기는 표(`LABEL_MAP`)만 맞추면 된다. 필요: `pip install transformers torch`.

    GARIM_NER_MODEL=./my-ner-model garim --l1-backend examples/backends/l1_hf_ner.py:build \\
        check '홍길동 고객의 주소는 서울시 중구 세종대로 110 입니다'

CPU 에서 기본 L1 예산(`limits.l1_timeout_ms` 1000ms)을 넘기면 그 청크는 L0 로만 판정되고
`degraded`(`l1_timeout`)가 된다 — 느린 모델이면 정책에서 예산을 늘린다.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from garim.detect.l1 import PII_LABEL_TO_CODE, L1Entity, L1Output
from garim.models import ContentType, Origin

# 자기 모델의 개체 라벨 → 가림 라벨(`PII_LABEL_TO_CODE` 의 키). 표에 없는 라벨은 버린다.
LABEL_MAP = {
    "PER": "PERSON",
    "PS": "PERSON",
    "LOC": "ADDRESS",
    "LC": "ADDRESS",
}


class HfNerBackend:
    name = "hf-ner"
    # 가림은 정책의 `limits.chunk_size`(기본 512)가 이 값을 넘으면 시작하지 않는다.
    max_length = 512

    def __init__(
        self,
        classify: Callable[[str], list[dict[str, Any]]],
        label_map: dict[str, str] | None = None,
    ) -> None:
        self._classify = classify
        self._label_map = dict(LABEL_MAP if label_map is None else label_map)
        unknown = set(self._label_map.values()) - set(PII_LABEL_TO_CODE)
        if unknown:
            raise ValueError(f"LABEL_MAP targets must be garim labels: {sorted(unknown)}")
        # 프록시는 요청마다 다른 스레드에서 같은 백엔드를 부른다. transformers 파이프라인은
        # 스레드 안전을 보장하지 않으므로 한 번에 하나만 돌린다.
        self._lock = threading.Lock()

    def infer(
        self,
        text: str,
        *,
        normalization_delta: dict[str, int],
        origin: Origin,
        content_type: ContentType,
    ) -> L1Output:
        del normalization_delta, origin, content_type
        with self._lock:
            predictions = self._classify(text)
        entities = []
        for item in predictions:
            label = self._label_map.get(str(item["entity_group"]))
            start, end = int(item["start"]), int(item["end"])
            if label is not None and start < end:
                entities.append(L1Entity(label, (start, end), float(item["score"])))
        return L1Output(entities=entities)


def build() -> HfNerBackend:
    model = os.environ.get("GARIM_NER_MODEL")
    if not model:
        raise ValueError("set GARIM_NER_MODEL to a local token-classification model directory")
    from transformers import pipeline

    return HfNerBackend(
        pipeline("token-classification", model=model, aggregation_strategy="simple")
    )
