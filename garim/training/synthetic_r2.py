"""R2 합성 공격 코퍼스 — 기본 문장 군(family) 단위 분할. PREREG_L1_R2 C1 · C2.

R1 의 `generate_attack_corpus` 는 `template_id` 를 인덱스 나머지(`atk-{label}-{i % 60}`,
`benign-{i % 200}`)로 정해서, 같은 기본 문장이 ID 여러 개로 흩어져 train·test 에 다 들어갔다.
행 지문은 꼬리말 번호 덕에 겹치지 않아 `validate_corpus` 도 통과했다(RESULTS_L1 §5-③).
R1 함수는 R1 재현용으로 그대로 두고, 여기서는:

- `template_id` = family. 표면 변이(어미 · 꼬리말 번호 · 은닉 · 로마자 · 존댓말)는 family 안이다.
- 공격 family 는 R1 템플릿 그대로(클래스당 3개)이고 **train · validation 에만** 둔다 — 공격
  문장을 새로 쓰지 않으므로 공격 홀드아웃이 없다(PREREG §8 벗어남). 공격 재현율은 외부 셋이 잰다.
- 정상 family 는 `benign_bank` 126개, train 70 · validation 10 · test 20.
- 로마자 · 존댓말은 **그 행 자신의 문장**을 변환한다. R1 은 고정 문장 둘을 공격 세 라벨에 20행씩
  넣어 같은 텍스트가 라벨 셋을 졌다. 정상 행에도 건다 — 변환이 공격의 지름길이 되지 않게.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict

from garim.training.benign_bank import BENIGN_FAMILIES
from garim.training.data import CorpusRecord, Split, validate_corpus
from garim.training.hiding import HOMOGLYPH, NONE, apply_hiding, planned_technique
from garim.training.synthetic import _ACTIONS, _ATTACK_TEMPLATES, _with_reference
from garim.training.t4 import honorify, romanize

ATTACK_ROWS_PER_CLASS = 200
BENIGN_ROWS = 2_400
# 행 인덱스로 정하는 변환 비율. 공격은 R1 과 같은 10% · 10%, 정상은 존댓말 10% · 로마자 5%.
_ATTACK_ROMANIZED, _ATTACK_HONORIFIC = 0, 1  # i % 10
_BENIGN_ROMANIZED = 0  # i % 20
_BENIGN_HONORIFIC = 5  # i % 10


def family_split(family: str, *, holdout: bool) -> Split:
    """family 해시로 분할한다. `holdout=False` 면 test 버킷을 train 으로 돌린다."""
    bucket = int(hashlib.sha256(f"r2-family:{family}".encode()).hexdigest()[:8], 16) % 10
    if bucket < 2:
        return "test" if holdout else "train"
    return "validation" if bucket == 2 else "train"


def _surface(text: str, index: int, *, romanized: bool, honorific: bool) -> str:
    if romanized:
        return romanize(text)
    if honorific:
        return honorify(text, index)
    return text


def generate_attack_corpus_r2(*, seed: int = 20260807) -> list[CorpusRecord]:
    rng = random.Random(seed)
    records: list[CorpusRecord] = []
    base_labels: dict[str, set[str]] = defaultdict(set)
    index = 0
    hiding_cursor = 0
    benign_cursor = 0

    def emit(
        label: str,
        family: str,
        base: str,
        local_index: int,
        *,
        romanized: bool,
        honorific: bool,
        benign: bool,
        holdout: bool,
    ) -> None:
        nonlocal index, hiding_cursor, benign_cursor
        base_labels[base].add(label)
        text = _with_reference(_surface(base, local_index, romanized=romanized,
                                        honorific=honorific), index)
        if romanized:
            # 로마자 행만 라틴 글자를 가지므로 공격 쪽은 R1 처럼 homoglyph 를 직접 건다.
            # 정상 스케줄에는 homoglyph 가 없으므로(hiding.py) 정상 로마자 행은 그대로 둔다.
            planned = NONE if benign else HOMOGLYPH
        elif benign:
            planned = planned_technique(benign_cursor, benign=True)
            benign_cursor += 1
        else:
            planned = planned_technique(hiding_cursor, benign=False)
            hiding_cursor += 1
        text, hiding = apply_hiding(text, planned)
        records.append(
            CorpusRecord(
                id=f"syn2-attack-{index:04d}",
                task="attack",
                text=text,
                source="garim-synthetic-attack",
                split=family_split(family, holdout=holdout),
                attack_label=label,  # type: ignore[arg-type]
                romanized_target=float(romanized),
                honorific_target=float(honorific),
                group_id=f"syn2-base-{index:04d}",
                template_id=family,
                meta={
                    "synthetic": True,
                    "gold_origin": "generator",
                    "generator": "r2",
                    "family": family,
                    "hiding": hiding,
                },
            )
        )
        index += 1

    for label, templates in _ATTACK_TEMPLATES.items():
        for local_index in range(ATTACK_ROWS_PER_CLASS):
            slot = local_index % len(templates)
            base = templates[slot].format(
                action=_ACTIONS[(local_index // len(templates)) % len(_ACTIONS)]
            )
            emit(
                label,
                f"atk-{label}-t{slot}",
                base,
                local_index,
                romanized=local_index % 10 == _ATTACK_ROMANIZED,
                honorific=local_index % 10 == _ATTACK_HONORIFIC,
                benign=False,
                holdout=False,
            )
    for local_index in range(BENIGN_ROWS):
        family, _, texts = BENIGN_FAMILIES[local_index % len(BENIGN_FAMILIES)]
        base = texts[(local_index // len(BENIGN_FAMILIES)) % len(texts)]
        emit(
            "NONE",
            family,
            base,
            local_index,
            romanized=local_index % 20 == _BENIGN_ROMANIZED,
            honorific=local_index % 10 == _BENIGN_HONORIFIC,
            benign=True,
            holdout=True,
        )

    conflicts = {base: labels for base, labels in base_labels.items() if len(labels) > 1}
    if conflicts:
        raise AssertionError(f"base sentence carries more than one label: {conflicts}")
    rng.shuffle(records)
    validate_corpus(records)
    return records
