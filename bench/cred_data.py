"""축 (d) 하네스 — `Samsung/CredData` 로 시크릿 플레이스홀더 판별을 잰다.

§6.4.1 의 축 (d) 는 "진짜 시크릿과 `YOUR_API_KEY`·`sk_test_...`·`${API_KEY}` 를
가르는 것" 이다. 즉 재현율이 아니라 **정밀도** 문제이고, CredData 가 정확히 그
분포다 — 라벨 73,842 줄 중 True 4,583(6%)이라 **음성이 69,259 줄**이다.
§2.4 가 인용한 detect-secrets precision 0.1416 도 이 벤치 수치다.

CredData 는 코드 파일을 따로 받아야 하지만 `meta/*.csv` 만으로도 라인 단위 평가가
된다 — 각 행이 `GroundTruth`(T/F/X)와 원문 라인의 값 범위를 담는다.

⚠️ 영어 코드 저장소 기준이다. §7.5 의 "한국어 산문 시크릿 벤치가 없다" 는 그대로
유효하고, 이 하네스는 **판별 능력** 측정용이지 한국어 성능 근거가 아니다.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# CredData 의 GroundTruth 코드. X 는 판정 보류라 평가에서 뺀다.
TRUE_LABEL = "T"
FALSE_LABEL = "F"
UNKNOWN_LABEL = "X"

Detector = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class CredRow:
    file_id: str
    line_number: int
    ground_truth: str
    category: str
    value: str

    @property
    def is_secret(self) -> bool:
        return self.ground_truth == TRUE_LABEL


@dataclass(slots=True)
class CredReport:
    detector: str
    rows: int
    positives: int
    negatives: int
    precision: float
    recall: float
    f1: float
    false_positives: int
    by_category: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "rows": self.rows,
            "positives": self.positives,
            "negatives": self.negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positives": self.false_positives,
            "by_category_recall": {k: round(v, 4) for k, v in sorted(self.by_category.items())},
        }


def load_meta(meta_dir: str | Path) -> list[CredRow]:
    """Read every `meta/*.csv`, keeping rows whose ground truth is decided."""
    directory = Path(meta_dir)
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise ValueError(f"no CredData meta CSV under {directory}")
    rows: list[CredRow] = []
    for path in files:
        with path.open(encoding="utf-8", newline="") as stream:
            for record in csv.DictReader(stream):
                truth = (record.get("GroundTruth") or "").strip()
                if truth not in {TRUE_LABEL, FALSE_LABEL}:
                    continue  # X(보류)와 헤더 잔재를 버린다
                value = (record.get("Value") or record.get("LineValue") or "").strip()
                if not value:
                    continue
                rows.append(
                    CredRow(
                        file_id=path.stem,
                        line_number=int(record.get("LineStart") or 0),
                        ground_truth=truth,
                        category=(record.get("Category") or "unknown").strip() or "unknown",
                        value=value,
                    )
                )
    if not rows:
        raise ValueError(f"{directory}: no decided rows (T/F) found")
    return rows


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(rows: Sequence[CredRow], detector: Detector, *, name: str) -> CredReport:
    """`detector(value)` returns True when it calls the candidate a real secret."""
    positives = [row for row in rows if row.is_secret]
    negatives = [row for row in rows if not row.is_secret]
    if not positives or not negatives:
        raise ValueError("CredData slice must contain both T and F rows")

    flagged = {(row.file_id, row.line_number, row.value): detector(row.value) for row in rows}

    def hit(row: CredRow) -> bool:
        return flagged[(row.file_id, row.line_number, row.value)]

    true_positive = sum(hit(row) for row in positives)
    false_positive = sum(hit(row) for row in negatives)
    precision = _rate(true_positive, true_positive + false_positive)
    recall = _rate(true_positive, len(positives))
    by_category: dict[str, list[bool]] = {}
    for row in positives:
        by_category.setdefault(row.category, []).append(hit(row))
    return CredReport(
        detector=name,
        rows=len(rows),
        positives=len(positives),
        negatives=len(negatives),
        precision=precision,
        recall=recall,
        f1=(2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
        false_positives=false_positive,
        by_category={key: sum(values) / len(values) for key, values in by_category.items()},
    )


def garim_secret_detector() -> Detector:
    """가림 L0 시크릿 규칙 — §2.4 의 3단 구조 중 (A)(B)."""
    from garim.detect.rules import detect_secrets

    def detector(value: str) -> bool:
        return bool(detect_secrets(value))

    return detector


def iter_placeholder_rows(rows: Iterable[CredRow]) -> Iterator[CredRow]:
    """플레이스홀더로 보이는 음성만 추린다 — 축 (d) 가 실제로 겨냥하는 부분집합."""
    markers = ("your", "example", "changeme", "placeholder", "xxxx", "<", "${", "test", "dummy")
    for row in rows:
        if row.is_secret:
            continue
        lowered = row.value.lower()
        if any(marker in lowered for marker in markers):
            yield row
