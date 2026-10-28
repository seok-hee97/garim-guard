"""H1 — 유출 축 유형별 탐지 F1 + FPR. §7.1.1 헤드라인 지표의 절반.

§7.6-2 가 *"재현율 옆에 항상 FPR — 예외 없다"* 라고 못박았으므로 이 하네스는 둘을
분리해 낼 수 없게 만들어져 있다. `LeakReport` 는 재현율만 담을 수 없다.

## 분모를 어디에 두는가 — §7.2.1

FPR 의 분모는 **음성 세그먼트 수**다. 스팬 수도, 전체 행 수도 아니다. 한 세그먼트에
오탐이 셋 나도 그 세그먼트 하나가 오탐이다 — 그렇게 세지 않으면 긴 문서 하나가
FPR 을 임의로 부풀린다.

## 문자 단위로 세는 이유 — §7.2

스팬 완전일치는 경계 한 글자 차이로 0점이 되고, 토큰 단위는 토크나이저에 의존한다.
마스킹 게이트웨이의 실제 손해는 **가린 글자와 못 가린 글자**이므로 문자가 맞는 단위다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from garim.training.data import CorpusRecord

Detector = Callable[[str], list[tuple[int, int, str]]]


@dataclass(frozen=True, slots=True)
class LabelScore:
    label: str
    gold_characters: int
    predicted_characters: int
    true_positive: int

    @property
    def precision(self) -> float:
        return self.true_positive / self.predicted_characters if self.predicted_characters else 0.0

    @property
    def recall(self) -> float:
        return self.true_positive / self.gold_characters if self.gold_characters else 0.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


@dataclass(slots=True)
class LeakReport:
    """★ 재현율과 FPR 이 한 객체에 같이 있다 — §7.6-2 를 구조로 강제한다."""

    detector: str
    records: int
    negative_segments: int
    false_positive_segments: int
    by_label: dict[str, LabelScore] = field(default_factory=dict)

    @property
    def segment_false_positive_rate(self) -> float:
        """§7.2.1 — 분모는 음성 **세그먼트** 수다."""
        return (
            self.false_positive_segments / self.negative_segments if self.negative_segments else 0.0
        )

    @property
    def macro_f1(self) -> float:
        scores = [item.f1 for item in self.by_label.values()]
        return sum(scores) / len(scores) if scores else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "records": self.records,
            "macro_f1": round(self.macro_f1, 4),
            # FPR 을 지표 표의 첫 줄 옆에 둔다 — 따로 찾아봐야 하는 자리에 두지 않는다.
            "segment_false_positive_rate": round(self.segment_false_positive_rate, 4),
            "negative_segments": self.negative_segments,
            "false_positive_segments": self.false_positive_segments,
            "by_label": {
                label: {
                    "precision": round(score.precision, 4),
                    "recall": round(score.recall, 4),
                    "f1": round(score.f1, 4),
                    "gold_characters": score.gold_characters,
                }
                for label, score in sorted(self.by_label.items())
            },
        }


def _characters(spans: Sequence[tuple[int, int, str]]) -> set[tuple[int, str]]:
    return {(position, label) for start, end, label in spans for position in range(start, end)}


def evaluate(records: Sequence[CorpusRecord], detector: Detector, *, name: str) -> LeakReport:
    """Character-level per-label scores plus the segment-level false-positive rate."""
    if not records:
        raise ValueError("leak evaluation needs at least one record")
    gold_total: dict[str, int] = defaultdict(int)
    predicted_total: dict[str, int] = defaultdict(int)
    hit_total: dict[str, int] = defaultdict(int)
    negative_segments = 0
    false_positive_segments = 0

    for record in records:
        gold = _characters([(s.start, s.end, s.label) for s in record.spans])
        predicted = _characters(detector(record.text))
        for _, label in gold:
            gold_total[label] += 1
        for _, label in predicted:
            predicted_total[label] += 1
        for item in gold & predicted:
            hit_total[item[1]] += 1
        # §7.2.1 — 음성 세그먼트에서 무엇이든 나오면 그 세그먼트가 오탐 하나다.
        if not record.spans:
            negative_segments += 1
            if predicted:
                false_positive_segments += 1

    labels = set(gold_total) | set(predicted_total)
    return LeakReport(
        detector=name,
        records=len(records),
        negative_segments=negative_segments,
        false_positive_segments=false_positive_segments,
        by_label={
            label: LabelScore(
                label=label,
                gold_characters=gold_total[label],
                predicted_characters=predicted_total[label],
                true_positive=hit_total[label],
            )
            for label in sorted(labels)
        },
    )


def garim_leak_detector(policy_path: str = "config/policy.yaml") -> Detector:
    """가림 L0 — `Finding` 을 (start, end, label) 로 옮긴다."""
    from garim import Inspector
    from garim.policy.engine import MASK_LABELS

    inspector = Inspector(policy_path)

    def detector(text: str) -> list[tuple[int, int, str]]:
        result = inspector.check(text)
        spans: list[tuple[int, int, str]] = []
        for finding in result.leaks:
            label = MASK_LABELS.get(finding.code)
            if label is None:
                continue  # 마스킹 라벨이 없는 코드(L-SEC-*, L-DOC-01)는 H1 대상이 아니다
            spans.append((finding.span[0], finding.span[1], label))
        return spans

    return detector


def write_report(path: str | Path, reports: Sequence[LeakReport]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"reports": [item.as_dict() for item in reports]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return destination
