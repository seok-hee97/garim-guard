"""Validated JSONL contract shared by corpus preparation, training, and evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from garim.training.export import PII_LABELS
from garim.training.split import fingerprint_text
from garim.training.teachers import ATTACK_LABELS, AttackLabel

Task = Literal["pii", "attack"]
Split = Literal["train", "validation", "test"]
PII_ENTITY_LABELS = frozenset(label[2:] for label in PII_LABELS if label.startswith("B-"))


class CorpusSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    label: str

    @model_validator(mode="after")
    def validate_span(self) -> CorpusSpan:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if self.label not in PII_ENTITY_LABELS:
            raise ValueError(f"unsupported PII label: {self.label}")
        return self


class CorpusRecord(BaseModel):
    """One pre-tokenization sample.

    PII rows may be all-negative and therefore have an empty ``spans`` list. Attack rows
    remain unlabeled until the teacher resolver fills ``attack_label``. Missing auxiliary
    targets use ``None`` here and become the training sentinel ``-1`` in the collator.
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    task: Task
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    split: Split
    spans: list[CorpusSpan] = Field(default_factory=list)
    attack_label: AttackLabel | None = None
    kanana_probabilities: tuple[float, float, float] | None = None
    qwen_jailbreak_target: float | None = Field(default=None, ge=0.0, le=1.0)
    romanized_target: float | None = Field(default=None, ge=0.0, le=1.0)
    honorific_target: float | None = Field(default=None, ge=0.0, le=1.0)
    group_id: str | None = None
    template_id: str | None = None
    pair_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_record(self) -> CorpusRecord:
        if self.task == "pii" and self.attack_label is not None:
            raise ValueError("PII rows cannot carry an attack label")
        if self.task == "attack" and self.spans:
            raise ValueError("attack rows cannot carry PII spans")
        if self.kanana_probabilities is not None:
            if any(not 0.0 <= value <= 1.0 for value in self.kanana_probabilities):
                raise ValueError("Kanana probabilities must be in [0, 1]")
            if sum(self.kanana_probabilities) <= 0:
                raise ValueError("Kanana probabilities must have positive mass")

        ordered = sorted(self.spans, key=lambda span: (span.start, span.end))
        for span in ordered:
            if span.end > len(self.text):
                raise ValueError(f"span {span} is outside text of length {len(self.text)}")
        for left, right in zip(ordered, ordered[1:], strict=False):
            if left.end > right.start:
                raise ValueError("PII spans must not overlap")
        return self


def iter_jsonl(path: str | Path) -> Iterator[CorpusRecord]:
    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                yield CorpusRecord.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: {exc}") from exc


def load_jsonl(path: str | Path) -> list[CorpusRecord]:
    return list(iter_jsonl(path))


def write_jsonl(path: str | Path, records: Iterable[CorpusRecord]) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(record.model_dump_json(exclude_none=True) + "\n")
            count += 1
    temporary.replace(destination)
    return count


def validate_corpus(records: Sequence[CorpusRecord]) -> dict[str, Any]:
    """Check IDs, group/template isolation, pair integrity, and exact text duplicates."""
    ids: set[str] = set()
    fingerprint_splits: dict[str, set[str]] = {}
    target_fingerprints: dict[str, str] = {}
    owners: dict[tuple[str, str], str] = {}
    pair_owners: dict[str, set[str]] = {}
    label_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()

    for record in records:
        if record.id in ids:
            raise ValueError(f"duplicate corpus id: {record.id}")
        ids.add(record.id)
        split_counts[record.split] += 1
        for span in record.spans:
            label_counts[span.label] += 1

        # ★ 지문이 분할을 넘는 것이 누수인가는 **그 행이 라벨을 지고 있느냐**에 달렸다.
        #
        #   공격 행은 텍스트 자체가 예측 대상(`attack_label`)이라 같은 문장이 양쪽에
        #   있으면 그대로 암기다 — 언제나 막는다.
        #
        #   PII 행 중 **스팬이 없는 것**은 다르다. 정답이 "전부 O" 이고, 그건 문장을
        #   보면 알 수 있는 다수 클래스다. KDPII 실측에서 분할을 넘는 지문 152개 중
        #   **스팬을 가진 것이 0개**였고 전부 `"네 맞습니다."` · `"정말 감사합니다!"`
        #   같은 일반 발화였다. 이런 발화는 어느 한국어 대화 코퍼스에나 반복해서
        #   나타나므로, 막으려면 그것을 공유하는 대화를 전부 한 덩어리로 이어야 하고
        #   그러면 분할이 통째로 무너진다(`"네"` 는 거의 모든 대화에 있다).
        #
        #   그래서 **좁게** 연다 — 라벨을 지지 않는 PII 행만. 스팬이 하나라도 있으면
        #   막고, 공격 행은 조건 없이 막는다.
        carries_a_target = bool(record.spans) or record.task != "pii"
        fingerprint = fingerprint_text(record.text)
        previous_splits = fingerprint_splits.setdefault(fingerprint, set())
        previous_target = target_fingerprints.get(fingerprint)
        other_splits = previous_splits - {record.split}
        if other_splits and (carries_a_target or previous_target is not None):
            previous_split = sorted(other_splits)[0]
            raise ValueError(
                f"normalized text fingerprint crosses {previous_split}/{record.split}: {record.id}"
            )
        previous_splits.add(record.split)
        if carries_a_target:
            target_fingerprints.setdefault(fingerprint, record.id)

        for kind, value in (("group", record.group_id), ("template", record.template_id)):
            if value is None:
                continue
            key = (kind, value)
            previous_split = owners.setdefault(key, record.split)
            if previous_split != record.split:
                raise ValueError(f"{kind} {value!r} crosses dataset splits")
        if record.pair_id is not None:
            pair_owners.setdefault(record.pair_id, set()).add(record.split)

    bad_pairs = {pair: splits for pair, splits in pair_owners.items() if len(splits) != 1}
    if bad_pairs:
        raise ValueError(f"paired positive/negative examples cross splits: {bad_pairs}")
    return {
        "records": len(records),
        "splits": dict(sorted(split_counts.items())),
        "span_labels": dict(sorted(label_counts.items())),
        "attack_labels": {
            label: sum(record.attack_label == label for record in records)
            for label in ATTACK_LABELS
        },
    }
