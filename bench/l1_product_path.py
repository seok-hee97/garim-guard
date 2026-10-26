"""PII 를 세 단계로 잰다 — L1 원출력 · 런타임 임계 적용 L1 · 제품 경로(`Inspector`).

`train/evaluate_l1.py` 는 표준 토큰 분류 지표다. argmax 가 O 가 아닌 토큰을 전부 개체로
센다. 그런데 런타임은 개체 점수(토큰 softmax 평균)에 0.9(주민·외국인등록번호는 0.7)를 곱해
`thresholds.l1_pii` 와 비교한다(`garim/detect/pipeline.py`). T4 에서 L1 원출력 개체의 점수
중앙값이 0.14 였다 — 원출력 지표는 런타임이 버리는 개체를 정밀도에서 깎고 재현율에서 더한다.
제품이 실제로 가리는 것은 세 번째 줄이다.

    python -m bench.l1_product_path --l1-model-dir train/artifacts/l1-onnx \
        --input data/corpus/kdpii-test.jsonl ... --output train/artifacts/l1-product-path.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from garim.detect.l1 import PII_LABEL_TO_CODE

CODE_TO_LABEL = {code: label for label, code in PII_LABEL_TO_CODE.items()}
# pipeline.py 와 같은 곱 — 주민·외국인등록번호는 형식 검증과 따로 가므로 더 깎는다.
LOW_TRUST_LABELS = {"KR_RRN", "KR_FRN"}


def runtime_confidence(label: str, score: float) -> float:
    return score * (0.7 if label in LOW_TRUST_LABELS else 0.9)


def _micro(gold: set[tuple[str, int, str]], predicted: set[tuple[str, int, str]]) -> dict[str, Any]:
    hit = len(gold & predicted)
    precision = hit / len(predicted) if predicted else 0.0
    recall = hit / len(gold) if gold else 0.0
    return {
        "gold_characters": len(gold),
        "predicted_characters": len(predicted),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "rows_with_prediction": len({item[0] for item in predicted}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--l1-model-dir", required=True)
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    from garim import Inspector
    from garim.detect.l1 import OnnxL1Backend
    from garim.normalize.chain import normalize
    from garim.normalize.offsets import to_source_span
    from garim.segment.chunk import chunk_text
    from garim.training.data import load_jsonl

    backend = OnnxL1Backend(args.l1_model_dir)
    inspector = Inspector(args.policy, l1_backend=backend)
    threshold = inspector.policy.config.thresholds.l1_pii

    gold: dict[str, set] = defaultdict(set)
    raw: dict[str, set] = defaultdict(set)
    gated: dict[str, set] = defaultdict(set)
    product: dict[str, set] = defaultdict(set)
    rows: dict[str, int] = defaultdict(int)
    for path in args.input:
        for record in load_jsonl(path):
            source = record.source
            rows[source] += 1
            for span in record.spans:
                gold[source].update((record.id, p, span.label) for p in range(span.start, span.end))
            bundle = normalize(record.text)
            for chunk in chunk_text(
                bundle.norm.text,
                chunk_size=backend.max_length,
                chunk_overlap=64,
                tokenizer=backend.tokenizer,
            ):
                output = backend.infer(
                    chunk.text,
                    normalization_delta=bundle.delta,
                    origin="user",
                    content_type="prose",
                )
                for entity in output.entities:
                    start, end = to_source_span(
                        (entity.span[0] + chunk.start, entity.span[1] + chunk.start),
                        bundle.norm.interval_map,
                        len(record.text),
                    )
                    chars = {(record.id, p, entity.label) for p in range(start, end)}
                    raw[source].update(chars)
                    if runtime_confidence(entity.label, entity.score) >= threshold:
                        gated[source].update(chars)
            for finding in inspector.check(record.text).leaks:
                label = CODE_TO_LABEL.get(finding.code)
                if label is not None:
                    start, end = finding.span
                    product[source].update((record.id, p, label) for p in range(start, end))

    report = {
        "policy": args.policy,
        "l1_pii_threshold": threshold,
        "by_source": {
            source: {
                "records": rows[source],
                "l1_raw": _micro(gold[source], raw[source]),
                "l1_runtime_threshold": _micro(gold[source], gated[source]),
                "product_path": _micro(gold[source], product[source]),
            }
            for source in sorted(rows)
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
