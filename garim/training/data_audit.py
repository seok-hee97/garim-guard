"""GPU 전 데이터 감사 — "파이프라인이 도는가" 가 아니라 "데이터가 재려는 것을 재는가".

R1 은 배선 · 행 지문 누수 · 메모리 · 자산 검사를 다 통과하고도 합성 test 가 학습 문장을 그대로
담고 있었다(RESULTS_L1 §5-③). 행 지문 검사는 꼬리말 번호(`참고 번호 NNNN`) 덕에 통과했다 — 행이
다르다는 것과 **문장이 다르다는 것**은 다른 검사다. 그리고 2표 합의가 정상 두 문장을 통째로
폐기한 것은 6b 보고서에 이미 수치로 있었는데 아무 게이트도 그것을 보지 않았다(§5-②).

여기서는 그 두 종류를 GPU 를 쓰기 전에 멈춘다.

- `audit_attack_synthetic` — 꼬리말을 떼고 가역 은닉을 정규화로 되돌린 **기본 문장**으로
  ① test 문장이 학습 쪽에 있는가(정확 일치와 문자 3-gram 근사 일치) ② 같은 문장이 라벨 둘 이상을
  지는가 ③ 라벨별 고유 기본 문장이 최소치를 넘는가.
- `audit_generator_consensus` — 합의 뒤 생성기 NONE 의 채택률과 family 별 폐기를 보고하고, 채택률이
  기준 아래면 멈춘다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from garim.normalize.chain import normalize
from garim.training.data import CorpusRecord

_REFERENCE = re.compile(r"\s*참고 번호 \d{4}\.?")
_SPACE = re.compile(r"\s+")
NEAR_DUPLICATE_JACCARD = 0.85


class DataAuditError(ValueError):
    """감사가 학습을 멈춰야 하는 결함을 찾았다."""


def base_text(text: str) -> str:
    """꼬리말 번호를 떼고 가역 은닉(영폭·자모·동형 문자·한자·전각·b64)을 되돌린 문장."""
    restored = normalize(text).norm.text
    return _SPACE.sub(" ", _REFERENCE.sub("", restored)).strip().casefold()


def _shingles(text: str) -> set[str]:
    compact = text.replace(" ", "")
    return {compact[index : index + 3] for index in range(max(1, len(compact) - 2))}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def audit_attack_synthetic(
    records: Sequence[CorpusRecord],
    *,
    min_distinct: Mapping[str, int] | None = None,
    near_duplicate: float = NEAR_DUPLICATE_JACCARD,
) -> dict[str, Any]:
    min_distinct = min_distinct or {"NONE": 100}
    bases: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    labels: dict[str, set[str]] = defaultdict(set)
    for record in records:
        base = base_text(record.text)
        bases[record.split][str(record.attack_label)].add(base)
        labels[base].add(str(record.attack_label))

    seen = {base for split in ("train", "validation") for group in bases[split].values()
            for base in group}
    held_out = {base for group in bases["test"].values() for base in group}
    exact = sorted(held_out & seen)
    seen_shingles = {base: _shingles(base) for base in seen}
    near = sorted(
        (round(score, 3), test_base, train_base)
        for test_base in held_out - seen
        for train_base, train_shingles in seen_shingles.items()
        if (score := _jaccard(_shingles(test_base), train_shingles)) >= near_duplicate
    )
    conflicts = {base: sorted(owned) for base, owned in labels.items() if len(owned) > 1}
    labels_set = {label for owned in labels.values() for label in owned}
    distinct = {
        label: len({base for split in bases.values() for base in split.get(label, set())})
        for label in sorted(labels_set)
    }
    short = {
        label: (distinct.get(label, 0), minimum)
        for label, minimum in min_distinct.items()
        if distinct.get(label, 0) < minimum
    }
    report = {
        "distinct_bases": distinct,
        "test_bases_seen_in_training": len(exact),
        "test_bases_near_duplicate_of_training": len(near),
        "label_conflicts": len(conflicts),
        "labels": sorted(labels_set),
    }
    problems = []
    if exact:
        problems.append(f"test 기본 문장 {len(exact)}개가 학습 쪽에도 있다 — 예: {exact[:2]}")
    if near:
        problems.append(f"test 기본 문장 {len(near)}개가 학습 문장과 근사 일치 — 예: {near[:2]}")
    if conflicts:
        problems.append(f"기본 문장 {len(conflicts)}개가 라벨 둘 이상 — 예: "
                        f"{list(conflicts.items())[:2]}")
    if short:
        problems.append(f"라벨별 고유 기본 문장 부족(실제, 최소): {short}")
    if problems:
        raise DataAuditError("; ".join(problems))
    return report


def audit_generator_consensus(
    audit_rows: Iterable[Mapping[str, Any]],
    generator_labels: Mapping[str, str],
    families: Mapping[str, str],
    *,
    minimum_benign_acceptance: float = 0.9,
) -> dict[str, Any]:
    """합의 감사 행에서 생성기 행만 골라 채택률과 family 별 폐기를 낸다."""
    outcome: Counter[tuple[str, str]] = Counter()
    dropped_by_family: Counter[str] = Counter()
    rows_by_family: Counter[str] = Counter()
    for row in audit_rows:
        gold = generator_labels.get(str(row["id"]))
        if gold is None:
            continue
        accepted = row["status"] == "accepted"
        outcome[(gold, str(row["label"]) if accepted else f"drop:{row.get('reason')}")] += 1
        if gold == "NONE":
            family = families.get(str(row["id"]), "?")
            rows_by_family[family] += 1
            dropped_by_family[family] += not accepted
    benign_total = sum(count for (gold, _), count in outcome.items() if gold == "NONE")
    benign_kept = outcome[("NONE", "NONE")]
    acceptance = benign_kept / benign_total if benign_total else 0.0
    whole_families = sorted(
        family for family, dropped in dropped_by_family.items()
        if dropped and dropped == rows_by_family[family]
    )
    report = {
        "generator_outcomes": {f"{gold}->{result}": count
                               for (gold, result), count in sorted(outcome.items())},
        "benign_acceptance": round(acceptance, 4),
        "benign_families_dropped_entirely": whole_families,
    }
    if benign_total and acceptance < minimum_benign_acceptance:
        raise DataAuditError(
            f"생성기 NONE 채택률 {acceptance:.3f} < {minimum_benign_acceptance} — "
            f"통째로 폐기된 family {whole_families[:5]}"
        )
    return report
