"""§7.2.3 / §8.1-8f — 실 한국어 코퍼스 경보율. 라벨 없이 재는 유일한 축.

§7.2.3 의 비대칭이 이 하네스의 전부다: **재현율은 정답 라벨이 있어야 재지만, 오탐은
히트만 보면 된다.** 자기가 만든 합성 음성으로는 §7.6-2 의 FPR 주장을 할 수 없다.

## 무엇을 재고 무엇을 못 재는가 — §7.2.3 이 못박은 것

`FPR = FP/(FP+TN)` 인데 실 코퍼스에서 TN 을 관측하지 못한다. 직접 재는 것은 둘이다.

    ① 세그먼트당 오탐 경보 수   ← 라벨 없이 잰다. 이 하네스가 내는 값
    ② 히트 중 진탐 비율(precision) ← 히트를 사람이 봐야 한다

**편향은 닫힌 형태이고 N 과 무관하다.** 음성 비율을 `1−p` 라 하면 `FP/N` 은 참 FPR 을
`p/(1−p)` 만큼 과소평가한다. L0 축은 실 코퍼스의 `p ~ 10⁻⁵` 이라 근사가 성립하고,
**L1 전담(PERSON/ADDRESS)만 깨진다** — 위키에 인명이 흔해 `p` 가 0.1~0.4 다.

★ **사람 판정을 하지 않는 운영에서는 ②를 낼 수 없다.** 그래서 이 하네스는 결과를
`FPR` 이라 부르지 않고 **`alert_rate`** 로 낸다. 이름을 바꾸는 것이 아니라, 재지 않은
것을 잰 것처럼 부르지 않기 위한 것이다. 히트 표본은 `sample_hits` 로 뽑아 두어
나중에 사람이 볼 수 있게 한다.

⚠️ L1 전담 코드(`L-CT-01/02/03`)는 별도 행으로 분리해 보고한다. 같은 표에 섞으면
근사가 깨지는 행이 성립하는 행에 묻힌다.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# §7.2.3 — 실 코퍼스에서 근사가 깨지는 축. 위키에 인명·지명이 흔하다.
L1_ONLY_CODES = frozenset({"L-CT-01", "L-CT-02", "L-CT-03"})


@dataclass(frozen=True, slots=True)
class Hit:
    segment_id: str
    code: str
    rule: str
    span: tuple[int, int]
    excerpt: str


@dataclass(slots=True)
class AlertReport:
    detector: str
    segments: int
    characters: int
    alerting_segments: int
    hits_by_code: dict[str, int] = field(default_factory=dict)
    l1_only_hits: int = 0

    @property
    def alert_rate(self) -> float:
        """세그먼트당 경보율. **FPR 이 아니다** — 모듈 docstring 참고."""
        return self.alerting_segments / self.segments if self.segments else 0.0

    @property
    def alerts_per_1000_segments(self) -> float:
        return self.alert_rate * 1000

    def as_dict(self) -> dict[str, Any]:
        l0_codes = {k: v for k, v in self.hits_by_code.items() if k not in L1_ONLY_CODES}
        l1_codes = {k: v for k, v in self.hits_by_code.items() if k in L1_ONLY_CODES}
        return {
            "detector": self.detector,
            "segments": self.segments,
            "characters": self.characters,
            # ★ 이름을 `false_positive_rate` 로 쓰지 않는다. 사람 판정 없이는
            #   히트가 전부 오탐이라고 단정할 수 없다(§7.2.3 의 ②를 안 쟀다).
            "alert_rate": round(self.alert_rate, 6),
            "alerts_per_1000_segments": round(self.alerts_per_1000_segments, 3),
            "alerting_segments": self.alerting_segments,
            "l0_axis_hits": dict(sorted(l0_codes.items())),
            # §7.2.3 — 이 행은 p/(1−p) 근사가 깨진다. 따로 적는다.
            "l1_only_axis_hits": dict(sorted(l1_codes.items())),
            "l1_only_note": (
                "L-CT-* 는 실 코퍼스에서 양성 비율이 0.1~0.4 라 FP/N 근사가 깨진다. "
                "무작위 비히트 표본 라벨링으로 p̂ 를 추정해야 FPR 로 환산할 수 있다."
            ),
            "measures": "alert rate only; hit precision needs human adjudication (§7.2.3 ②)",
        }


def iter_text_corpus(path: str | Path, *, field_name: str = "text") -> Iterator[tuple[str, str]]:
    """`.jsonl`(지정 필드) 또는 줄 단위 `.txt` 에서 (id, text) 를 흘린다."""
    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if source.suffix == ".jsonl":
                payload = json.loads(stripped)
                text = payload.get(field_name)
                if not isinstance(text, str) or not text.strip():
                    continue
                yield str(payload.get("id", f"{source.stem}:{line_number}")), text
            else:
                yield f"{source.stem}:{line_number}", stripped


def scan(
    segments: Iterable[tuple[str, str]],
    detector: Callable[[str], list[Hit]],
    *,
    name: str,
    collect: list[Hit] | None = None,
) -> AlertReport:
    """전량에 검사기를 돌리고 히트만 센다 — §7.2.3 절차 ①②."""
    counts: Counter[str] = Counter()
    total = characters = alerting = l1_only = 0
    for segment_id, text in segments:
        total += 1
        characters += len(text)
        hits = detector(text)
        if not hits:
            continue
        alerting += 1
        for hit in hits:
            counts[hit.code] += 1
            if hit.code in L1_ONLY_CODES:
                l1_only += 1
            if collect is not None:
                collect.append(
                    Hit(segment_id, hit.code, hit.rule, hit.span, hit.excerpt)
                )
    if not total:
        raise ValueError("corpus is empty")
    return AlertReport(
        detector=name,
        segments=total,
        characters=characters,
        alerting_segments=alerting,
        hits_by_code=dict(counts),
        l1_only_hits=l1_only,
    )


def garim_detector(policy_path: str = "config/policy.yaml") -> Callable[[str], list[Hit]]:
    from garim import Inspector

    inspector = Inspector(policy_path)

    def detector(text: str) -> list[Hit]:
        result = inspector.check(text)
        return [
            Hit(
                segment_id="",
                code=finding.code,
                rule=finding.rule,
                span=finding.span,
                # ★ 원문을 통째로 남기지 않는다. 판정에 필요한 최소 문맥만 자른다.
                excerpt=text[max(0, finding.span[0] - 24) : finding.span[1] + 24],
            )
            for finding in (*result.leaks, *result.attacks)
        ]

    return detector


def sample_hits(hits: Sequence[Hit], size: int, *, seed: int = 13) -> list[Hit]:
    """사람 판정용 무작위 부표본.

    §7.2.3 이 "L1 전담 축은 무작위 부표본 2,000건만 판정" 이라고 규모를 정했다 —
    히트 33,500건 전수 판정은 8f 예산으로 불가능하다.
    """
    if size >= len(hits):
        return list(hits)
    return random.Random(seed).sample(list(hits), size)


def write_adjudication_queue(path: str | Path, hits: Sequence[Hit]) -> int:
    """판정 큐. **원문 전체가 아니라 발췌만** 나간다(§3.2 — 원문을 로그로 남기지 않는다)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        for hit in hits:
            stream.write(
                json.dumps(
                    {
                        "segment_id": hit.segment_id,
                        "code": hit.code,
                        "rule": hit.rule,
                        "excerpt": hit.excerpt,
                        "verdict": None,  # true_positive | false_positive
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return len(hits)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="§7.2.3 실 코퍼스 경보율")
    parser.add_argument("--corpus", required=True, help=".jsonl 또는 .txt")
    parser.add_argument("--field", default="text")
    parser.add_argument("--policy", default="config/policy.yaml")
    parser.add_argument("--queue", help="사람 판정용 히트 큐 출력 경로")
    parser.add_argument("--queue-size", type=int, default=2000)
    parser.add_argument("--output")
    args = parser.parse_args()

    collected: list[Hit] = []
    report = scan(
        iter_text_corpus(args.corpus, field_name=args.field),
        garim_detector(args.policy),
        name="garim-l0",
        collect=collected,
    )
    payload = report.as_dict()
    print(f"세그먼트 {payload['segments']:,} · 문자 {payload['characters']:,}")
    print(
        f"경보 세그먼트 {payload['alerting_segments']:,} · "
        f"경보율 {payload['alert_rate']:.6f} "
        f"({payload['alerts_per_1000_segments']:.2f} / 1,000 세그먼트)"
    )
    print(f"L0 축 히트 : {payload['l0_axis_hits']}")
    print(f"L1 전담 히트: {payload['l1_only_axis_hits']}  ← FP/N 근사가 깨지는 행")
    print("⚠️ 경보율은 FPR 이 아니다 — 히트 중 진탐 비율(§7.2.3 ②)은 사람 판정이 필요하다.")
    if args.queue:
        count = write_adjudication_queue(
            args.queue, sample_hits(collected, args.queue_size)
        )
        print(f"판정 큐 {count}건 -> {args.queue}")
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
