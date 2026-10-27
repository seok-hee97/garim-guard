"""PREREG_L1_R2 의 새 홀드아웃 지표 — family 단위 cluster bootstrap 구간과 함께.

사전 등록 §4 중 새 셋에서 나오는 것만 잰다. 외부 셋(KoreanGuardrail · KDPII · KLUE · bc-pii-ko)은
기존 하네스(`bench.run_axis_b`, `bench.l1_product_path`)가 그대로 잰다.

| 지표 | 셋 | 절차 |
|---|---|---|
| O2 정상 FPR | `t4-r2-test` | `evaluate_onnx_records` 와 같은 공격 판정 |
| O3 PII 음성 오탐 행 | `pii-negative-r2-test` 음성 두 범주 | 제품 경로 — 예측이 나온 행 |
| O4 계좌 짝 양성 R | `pii-negative-r2-test` 계좌 짝 양성 | 제품 경로 KR_ACCOUNT 문자 재현율 |
| S4 PII 오탐 행 | `t4-r2-test` | 제품 경로 |

행 수는 n 이 아니다(§7.6-4) — 모든 비율에 family 수와 family 를 복원 추출한 2,000회 bootstrap 의
95% 구간을 붙인다. 제품 경로는 임계 근처 개체가 CPU 에 따라 갈리므로 기계 정보도 남긴다.

    python -m bench.r2_eval --l1-model-dir train/artifacts/l1-onnx \\
        --output train/artifacts/r2-baseline-r1-mac.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from garim.detect.l1 import PII_LABEL_TO_CODE

CODE_TO_LABEL = {code: label for label, code in PII_LABEL_TO_CODE.items()}
BOOTSTRAP_ROUNDS = 2_000
BOOTSTRAP_SEED = 20261027


def cluster_bootstrap(
    units: Sequence[tuple[str, float, float]],
    *,
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """`units` 는 (family, 분자, 분모). family 를 복원 추출해 비율 Σ분자/Σ분모 의 구간을 낸다."""
    by_family: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for family, numerator, denominator in units:
        by_family[family][0] += numerator
        by_family[family][1] += denominator
    families = sorted(by_family)
    total = [sum(by_family[f][0] for f in families), sum(by_family[f][1] for f in families)]
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(rounds):
        drawn = [by_family[rng.choice(families)] for _ in families]
        denominator = sum(item[1] for item in drawn)
        if denominator:
            estimates.append(sum(item[0] for item in drawn) / denominator)
    estimates.sort()
    return {
        "value": round(total[0] / total[1], 4) if total[1] else None,
        "numerator": total[0],
        "denominator": total[1],
        "families": len(families),
        "ci95": [
            round(estimates[int(0.025 * (len(estimates) - 1))], 4),
            round(estimates[int(0.975 * (len(estimates) - 1))], 4),
        ]
        if estimates
        else None,
    }


def _family(record: Any) -> str:
    return str(record.meta.get("family") or record.template_id or record.id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--l1-model-dir", required=True)
    parser.add_argument("--model-file", default="garim_l1.int8.onnx")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--t4", default="data/corpus/t4-r2-test.jsonl")
    parser.add_argument("--pii-negative", default="data/corpus/pii-negative-r2-test.jsonl")
    parser.add_argument("--output")
    args = parser.parse_args()

    import onnxruntime

    from garim import Inspector
    from garim.detect.l1 import OnnxL1Backend
    from garim.training.data import load_jsonl
    from garim.training.onnx_evaluation import evaluate_onnx_records

    backend = OnnxL1Backend(args.l1_model_dir, model_filename=args.model_file)
    inspector = Inspector(args.policy, l1_backend=backend)

    def attack_prediction(record: Any) -> str:
        confusion = evaluate_onnx_records(backend, [record])["attack_confusion"]
        row = confusion[record.attack_label]
        return next(label for label, count in row.items() if count)

    def pii_spans(text: str) -> list[tuple[str, int, int]]:
        return [
            (CODE_TO_LABEL[finding.code], *finding.span)
            for finding in inspector.check(text).leaks
            if finding.code in CODE_TO_LABEL
        ]

    t4 = load_jsonl(args.t4)
    false_positive: list[tuple[str, float, float]] = []
    t4_pii_rows: list[tuple[str, float, float]] = []
    by_cell: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in t4:
        if record.attack_label != "NONE":
            raise ValueError(f"{record.id}: t4-r2-test must be benign only")
        flagged = attack_prediction(record) != "NONE"
        false_positive.append((_family(record), float(flagged), 1.0))
        cell = by_cell[str(record.meta["cell"])]
        cell[0] += flagged
        cell[1] += 1
        t4_pii_rows.append((_family(record), float(bool(pii_spans(record.text))), 1.0))

    negatives: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    account: list[tuple[str, float, float]] = []
    for record in load_jsonl(args.pii_negative):
        kind = str(record.meta["type"])
        predicted = pii_spans(record.text)
        if kind == "account_pair_positive":
            gold = {p for span in record.spans for p in range(span.start, span.end)}
            hit = {
                p for label, start, end in predicted if label == "KR_ACCOUNT"
                for p in range(start, end)
            }
            account.append((_family(record), float(len(gold & hit)), float(len(gold))))
        else:
            negatives[kind].append((_family(record), float(bool(predicted)), 1.0))

    summarize = cluster_bootstrap
    report = {
        "model": f"{args.l1_model_dir}/{args.model_file}",
        "policy": args.policy,
        "machine": {"platform": platform.platform(), "machine": platform.machine(),
                    "onnxruntime": onnxruntime.__version__},
        "O2_t4_r2_benign_fpr": {
            **summarize(false_positive),
            "by_cell": {cell: round(n / d, 4) for cell, (n, d) in sorted(by_cell.items())},
        },
        "O3_pii_negative_rows_with_prediction": {
            "all_negatives": summarize([u for units in negatives.values() for u in units]),
            **{kind: summarize(units) for kind, units in sorted(negatives.items())},
        },
        "O4_account_pair_positive_char_recall": summarize(account),
        "S4_t4_r2_rows_with_pii": summarize(t4_pii_rows),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
