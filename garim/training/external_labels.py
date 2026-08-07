"""외부 원천 라벨 → 프로젝트 4-way 택소노미. **평가 전용, 결정적 매핑.**

사람 검수를 하지 않기로 한 이상 최종 test gold 의 출처를 정해야 한다. 후보는 셋인데
둘은 못 쓴다.

- **교사 합의 라벨** — 학생을 자기 교사로 채점하는 순환이다. §7.6-3(동어반복 금지) 위반.
- **PolyGuard harm 라벨** — 택소노미가 다르다. `harmful/unharmful` 이 인젝션·탈옥·
  시스템프롬프트 유출로 결정적으로 갈리지 않는다. §5.2 어댑터가 *"source harm labels
  are not project attack labels"* 라고 적어둔 그대로이며, 여기서도 **거부**한다.
- **deepset 이진 라벨** — `injection`/`legitimate` 로 이미 사람이 붙인 라벨이고
  인젝션 축에 1:1 대응한다. 이것만 받는다.

즉 사람 검수 없이 확보되는 외부 gold 는 **인젝션 축의 이진 판정뿐**이다. 4-way macro
는 구성상 정답이 알려진 합성 test 로만 보고하고, 그 사실을 리포트에 병기한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from garim.training.data import CorpusRecord
from garim.training.teachers import AttackLabel

# deepset-prompt-injections 의 원천 라벨. 정수(0/1)와 문자열 표기를 모두 받는다.
# 파이썬에서 True 는 1, False 는 0 과 같은 키다 — 따로 적지 않는다.
_DEEPSET_BINARY: dict[object, AttackLabel] = {
    1: "INJECTION",
    "1": "INJECTION",
    "injection": "INJECTION",
    0: "NONE",
    "0": "NONE",
    "legitimate": "NONE",
    "clean": "NONE",
}

MAPPABLE_SOURCES = frozenset({"deepset-prompt-injections"})


class UnmappableTaxonomyError(ValueError):
    """The source's label space does not resolve to the project's four classes."""


def map_deepset_label(value: object) -> AttackLabel:
    key = value.strip().lower() if isinstance(value, str) else value
    try:
        return _DEEPSET_BINARY[key]
    except (KeyError, TypeError) as exc:
        raise UnmappableTaxonomyError(f"unknown deepset label: {value!r}") from exc


def apply_external_eval_labels(
    records: Iterable[CorpusRecord], *, meta_key: str = "original_binary_label"
) -> Iterator[CorpusRecord]:
    """Fill `attack_label` on sealed test rows from their own source label.

    Refuses any source whose taxonomy is not 1:1 with ours rather than guessing — a wrong
    gold label is worse than a missing one because it silently moves the headline number.
    """
    for record in records:
        if record.split != "test":
            raise ValueError(f"{record.id}: external gold applies to sealed test rows only")
        if record.task != "attack":
            raise ValueError(f"{record.id}: attack rows only")
        if record.source not in MAPPABLE_SOURCES:
            raise UnmappableTaxonomyError(
                f"{record.id}: {record.source} labels are not 1:1 with the four classes; "
                "use the synthetic test set for 4-way metrics"
            )
        if record.attack_label is not None:
            raise ValueError(f"{record.id}: row already carries a label")
        if meta_key not in record.meta:
            raise ValueError(f"{record.id}: no {meta_key} to map from")
        yield record.model_copy(
            update={
                "attack_label": map_deepset_label(record.meta[meta_key]),
                "meta": {
                    **record.meta,
                    "requires_manual_label": False,
                    "gold_source": "external-binary",
                    "gold_taxonomy": "deepset injection/legitimate",
                },
            }
        )
