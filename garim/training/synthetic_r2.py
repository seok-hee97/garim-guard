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

GPU 전 독립 검토가 찾은 셋을 고쳤다(PREREG §8).

- **꼬리말 번호가 라벨을 알려 줬다.** 번호가 생성 순서의 전역 인덱스라 공격 0~599 · 정상 600~ 로
  갈렸다(R1 도 같다). 번호는 은닉을 다 통과하므로 모델이 문장 대신 번호를 배울 수 있었다. 이제
  라벨과 무관한 난수 순열에서 뽑는다.
- **`t4-r2-test` 정상 셀의 은닉을 학습 정상문이 한 번도 갖지 않았다.** R1 스케줄은 정상에 전각 ·
  한자만 넣는다 — 그러면 영폭 · 자모 · 동형 문자 · 어절 쪼개기 · 된소리는 학습에서 공격으로만
  나오고, O2 는 모델과 무관하게 통과할 수 없다(R1 어절 쪼개기 셀 FPR 0.93). 정상 스케줄에 다섯을
  3% 씩 넣는다. b64 는 넣지 않는다 — 감싸는 틀이 "다음 지시를 실행하세요:" 이고 R1 셀 FPR 도
  0 이었다. 초성 · 띄어쓰기 제거도 3% 씩 — 정상 예시가 0 이었는데 KoreanGuardrail 정상 행에
  둘이 816행 있다(O1 과방어).
- 동형 문자는 라틴 글자가 있어야 걸린다. 정상 한국어 행에 중립 약어를 심었더니 그 접두가 정상에만
  있어서 새 지름길이 됐다(재검토). 학습 접두는 T4 정상 셀(`CSV 첨부:`)과 다른 말을 쓰고, 공격 ·
  정상 로마자 행 절반에도 같은 접두를 붙이고, 정상 로마자 행에도 동형 문자를 건다.
- **seed 선택용 검증에 공격 합성이 없었다.** 7단계가 family 해시로 다시 나누는데 공격 family 9개가
  전부 train 에 떨어져, 검증 SYSTEM_LEAK · JAILBREAK 가 몇 행뿐이었다. 클래스마다 템플릿 하나(t2)를
  검증에 고정하고 `meta.split_pinned` 로 7단계가 그 분할을 지키게 한다.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict

from garim.training.benign_bank import BENIGN_FAMILIES
from garim.training.data import CorpusRecord, Split, validate_corpus
from garim.training.hiding import (
    CHOSUNG,
    COMPAT_FORMS,
    HANJA,
    HOMOGLYPH,
    JAMO,
    NONE,
    SPACE_BREAK,
    SPACE_JOIN,
    TENSIFY,
    ZWSP,
    _schedule,
    apply_hiding,
    planned_technique,
)
from garim.training.synthetic import _ACTIONS, _ATTACK_TEMPLATES, _with_reference
from garim.training.t4 import honorify, romanize

ATTACK_ROWS_PER_CLASS = 200
BENIGN_ROWS = 2_400
# 행 인덱스로 정하는 변환 비율. 공격은 R1 과 같은 10% · 10%, 정상은 존댓말 10% · 로마자 5%.
_ATTACK_ROMANIZED, _ATTACK_HONORIFIC = 0, 1  # i % 10
_BENIGN_ROMANIZED = 0  # i % 20
_BENIGN_HONORIFIC = 5  # i % 10
# 정상 은닉 — R1 의 전각 · 한자(15%)에 `t4-r2-test` 정상 셀의 은닉 다섯을 3% 씩.
BENIGN_WEIGHTS_R2: tuple[tuple[str, int], ...] = (
    (NONE, 64),
    (COMPAT_FORMS, 8),
    (HANJA, 7),
    (ZWSP, 3),
    (JAMO, 3),
    (SPACE_BREAK, 3),
    (TENSIFY, 3),
    (HOMOGLYPH, 3),
    (CHOSUNG, 3),
    (SPACE_JOIN, 3),
)
# 동형 문자용 중립 접두 — T4 정상 셀(`t4._BENIGN_LATIN_MARKERS` · "첨부")과 겹치지 않는 말.
# 글자마다 동형 문자가 있어야 치환이 걸린다 — "URL" 은 안 걸려 접두만 남은 정상 행이 생겼다.
_TRAIN_LATIN_MARKERS = ("XLSX", "HTML", "XML", "CSS")


def _latin_prefix(text: str, index: int) -> str:
    return f"{_TRAIN_LATIN_MARKERS[index % len(_TRAIN_LATIN_MARKERS)]} 자료: {text}"
_BENIGN_SCHEDULE_R2 = _schedule(BENIGN_WEIGHTS_R2)
# 검증에 고정하는 공격 템플릿 — 클래스마다 하나.
VALIDATION_ATTACK_SLOT = 2


def family_split(family: str, *, holdout: bool) -> Split:
    """family 해시로 분할한다. `holdout=False` 면 test 버킷을 train 으로 돌린다."""
    if family.startswith("atk-"):
        return "validation" if family.endswith(f"-t{VALIDATION_ATTACK_SLOT}") else "train"
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
    total = ATTACK_ROWS_PER_CLASS * len(_ATTACK_TEMPLATES) + BENIGN_ROWS
    # 꼬리말 번호 — 생성 순서와 무관한 순열. 순서대로 매기면 번호 구간이 라벨을 알려 준다.
    references = random.Random(seed + 1).sample(range(10_000), total)
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
                                        honorific=honorific), references[index])
        if romanized:
            # 로마자 행은 라틴 글자를 가지므로 공격 · 정상 모두 homoglyph 를 직접 건다. 절반에는
            # 정상 한국어 동형 문자 행과 같은 접두를 붙여 접두가 라벨을 따라가지 않게 한다.
            planned = HOMOGLYPH
            # 로마자 행의 인덱스는 공격 i % 10 == 0, 정상 i % 20 == 0 이라 i % 4 로는 정상이 전부
            # 한쪽에 몰린다 — 로마자 행 안에서 센 순번의 홀짝으로 나눈다.
            if (local_index // (20 if benign else 10)) % 2 == 0:
                text = _latin_prefix(text, local_index)
        elif not benign and local_index % 33 == 5:
            # 한글 본문 + 동형 문자 + 접두 조합이 정상에만 있으면 `t4-r2-test` 동형 문자 셀이 그
            # 조합을 보상한다(재검토 3차). 로마자가 아닌 공격 행 약 3% 에도 같은 처리를 한다.
            planned = HOMOGLYPH
            text = _latin_prefix(text, local_index)
        elif benign:
            planned = _BENIGN_SCHEDULE_R2[benign_cursor % 100]
            benign_cursor += 1
            if planned == HOMOGLYPH:
                # 정상 한국어 문장에는 바꿀 라틴 글자가 없다 — 중립 약어를 심는다.
                text = _latin_prefix(text, local_index)
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
                    # 7단계 성분 분할이 다시 나누지 않는다 — family 분할이 기준이다.
                    "split_pinned": True,
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
