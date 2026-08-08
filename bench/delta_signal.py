"""§7.2 S3 — 정규화 델타의 판별력. 고정밀 4종이 은닉 시도에 실제로 기여하는가.

§3.2.5 가 8차원 델타를 **고정밀 4종**과 저정밀 4종으로 갈라놓고 이렇게 단언했다.

> 고정밀 4종(`zero_width`·`tag_chars`·`homoglyph`·`jamo_composed`) 중 하나라도 > 0 이면
> **은닉 시도 신호**다. 저정밀은 신호가 아니라 재검사 수단이다.

이 문장은 두 가지를 동시에 주장한다 — 고정밀이 은닉을 **잡는다**는 것과, 평범한 글에서는
**안 울린다**는 것. 둘 다 재야 한다. 한쪽만 재면 §7.6-2 가 금지한 "재현율만 보고" 다.

## 기법별로 미리 예측한다

KoreanGuardrail 의 `technique` 축이 §2.2 은닉 축과 대응한다(train/sources.yaml). 이
대응이 곧 **반증 가능한 예측**이 된다 — 어느 칸이 울리고 어느 칸이 안 울려야 하는지가
측정 전에 정해져 있다.

| technique | 대응 코드 | 고정밀이 울려야 하나 | 왜 |
|---|---|---|---|
| `jamo_decompose` | A-KO-01 | **예** | `jamo_composed` 가 센다 |
| `zwsp_inject` | A-ENC-02 | **예** | `zero_width` 가 센다 |
| `break_spacing` | A-KO-05 | **아니오** | `space` 는 **저정밀**이다 |
| `chosung` | A-KO-02 | **아니오** | 초성화는 가역이 아니라 델타로 안 잡힌다 |
| `tensify` | (코드 없음) | **아니오** | `detensified` 스트림으로 처리하고 델타에 안 넣었다 |
| `clean` | — | **아니오** | 오경보 분모 |

**아니오 칸이 울리면 그게 문제다.** 저정밀·비가역 기법을 고정밀 신호가 잡는다면 신호가
기법을 구분하지 못한다는 뜻이고, 그러면 §3.2.5 의 고정밀/저정밀 구분 자체가 무의미해진다.

## 평범한 한국어에서 안 울리는가

`clean` 505행만으로는 부족하다 — 같은 저자가 만든 같은 문체다. 실제 한국어로 다시
확인해야 오경보율이 의미를 갖는다. KLUE(CC BY-SA 4.0, `train/sources.yaml` 등재)를 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# §3.2.5 가 지정한 고정밀 4종. 나머지 넷은 재검사 수단이지 신호가 아니다.
HIGH_PRECISION = ("zero_width", "tag_chars", "homoglyph", "jamo_composed")
LOW_PRECISION = ("nfkc", "hanja", "space", "b64_decoded")

# 측정 전에 박는 예측. 결과를 보고 고치면 예측이 아니다.
SHOULD_FIRE = {"jamo_decompose": True, "zwsp_inject": True}
SHOULD_NOT_FIRE = {"break_spacing": False, "chosung": False, "tensify": False, "clean": False}
EXPECTATION = {**SHOULD_FIRE, **SHOULD_NOT_FIRE}
# "울린다/안 울린다" 의 경계. 예측을 세우면서 같이 정한다.
FIRE_THRESHOLD = 0.80
QUIET_THRESHOLD = 0.05


@dataclass(slots=True)
class Tally:
    rows: int = 0
    high_precision_hits: int = 0
    low_precision_hits: int = 0
    by_feature: dict[str, int] = field(default_factory=dict)

    @property
    def high_rate(self) -> float:
        return self.high_precision_hits / self.rows if self.rows else 0.0

    @property
    def low_rate(self) -> float:
        return self.low_precision_hits / self.rows if self.rows else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "high_precision_rate": round(self.high_rate, 4),
            "low_precision_rate": round(self.low_rate, 4),
            "by_feature": dict(sorted(self.by_feature.items())),
        }


def tally(texts: Iterable[str]) -> Tally:
    from garim.normalize.chain import normalize

    result = Tally()
    for text in texts:
        delta = normalize(text).delta
        result.rows += 1
        fired = [name for name in HIGH_PRECISION if delta.get(name, 0) > 0]
        result.high_precision_hits += bool(fired)
        result.low_precision_hits += any(delta.get(name, 0) > 0 for name in LOW_PRECISION)
        for name in fired:
            result.by_feature[name] = result.by_feature.get(name, 0) + 1
    return result


def anti_correlated_features(results: dict[str, Any]) -> list[str]:
    """★ 저정밀보다 나쁜 것 — 우회문에서 **덜** 울리는 칸을 찾는다.

    `space` 가 그렇다. 이 데이터셋의 `break_spacing` 은 공백을 **붙여쓰기로 제거**하고,
    `space` 델타는 정규화가 **제거한** 공백을 센다. 그래서 우회문은 0 이고 평범한 글은
    7 이 나온다 — 방향이 뒤집혀 있다.

    §3.2.5 는 이걸 "저정밀" 로 분류했지만 저정밀은 *약한 신호* 라는 뜻이지 *반대 신호*
    라는 뜻이 아니다. §6.2 가 이 8차원을 L1 의 보조 feature 로 넣겠다고 했으므로
    그냥 두면 모델이 **"공백이 안 지워졌다 → 무해하다"** 를 배울 수 있다.

    (break_spacing 자체는 `nospace` 스트림이 잡는다 — 델타가 아니라 재검사 경로다.
     그래서 탐지에는 구멍이 없고, 문제는 이 칸을 feature 로 쓸 때 생긴다.)
    """
    baseline = results.get("clean", {}).get("low_precision_rate", 0.0)
    flagged = []
    for technique, expected in EXPECTATION.items():
        if expected or technique == "clean":
            continue
        rate = results.get(technique, {}).get("low_precision_rate")
        if rate is not None and rate < baseline / 2:
            flagged.append(technique)
    return flagged


def verdict(technique: str, rate: float) -> tuple[str, bool]:
    """예측과 맞는가. 예측은 위에 박아둔 것이고 여기서 만들지 않는다."""
    expected = EXPECTATION.get(technique)
    if expected is None:
        return "예측 없음", True
    if expected:
        ok = rate >= FIRE_THRESHOLD
        return f"울려야 함(≥{FIRE_THRESHOLD:.0%}) — {'맞음' if ok else '★ 빗나감'}", ok
    ok = rate <= QUIET_THRESHOLD
    return f"조용해야 함(≤{QUIET_THRESHOLD:.0%}) — {'맞음' if ok else '★ 빗나감'}", ok


def _klue_sentences(limit: int) -> list[str]:
    """평범한 한국어. KoreanGuardrail 의 `clean` 은 같은 저자·같은 문체라 부족하다."""
    from datasets import load_dataset

    data = load_dataset("klue/klue", "ynat", split="validation")
    return [row["title"] for row in data.select(range(min(limit, len(data))))]


def _guardrail_rows() -> Sequence[dict[str, Any]]:
    from datasets import load_dataset

    return load_dataset(
        "kimchunsik03/KoreanGuardrail",
        "benchmark",
        split="test",
        revision="cd4c93cbd39bd481c9e9bebd3686c56bfc2af3e4",
    )


def main() -> int:
    import argparse
    from collections import defaultdict

    parser = argparse.ArgumentParser(description="§7.2 S3 정규화 델타 판별력")
    parser.add_argument("--klue", type=int, default=3000)
    parser.add_argument("--output", default="artifacts/delta_signal.json")
    args = parser.parse_args()

    grouped: dict[str, list[str]] = defaultdict(list)
    for row in _guardrail_rows():
        grouped[row["technique"]].append(row["text"])

    results: dict[str, Any] = {}
    all_ok = True
    print(f"{'technique':<18}{'n':>6}{'고정밀':>9}{'저정밀':>9}  판정")
    for technique in sorted(grouped, key=lambda name: (EXPECTATION.get(name) is not True, name)):
        counts = tally(grouped[technique])
        text, ok = verdict(technique, counts.high_rate)
        all_ok &= ok
        results[technique] = {**counts.as_dict(), "verdict": text, "matches_prediction": ok}
        print(
            f"{technique:<18}{counts.rows:>6}{counts.high_rate:>9.1%}"
            f"{counts.low_rate:>9.1%}  {text}"
        )

    klue = tally(_klue_sentences(args.klue))
    klue_text, klue_ok = verdict("clean", klue.high_rate)
    all_ok &= klue_ok
    results["klue_real_korean"] = {
        **klue.as_dict(),
        "verdict": klue_text,
        "matches_prediction": klue_ok,
    }
    print(
        f"\n{'KLUE(실제 한국어)':<18}{klue.rows:>6}{klue.high_rate:>9.1%}"
        f"{klue.low_rate:>9.1%}  {klue_text}"
    )

    print(f"\n§3.2.5 의 고정밀/저정밀 구분: {'예측대로다' if all_ok else '★ 어긋난 칸이 있다'}")
    inverted = anti_correlated_features(results)
    if inverted:
        print(
            f"★ 방향이 뒤집힌 칸이 있다 — {', '.join(inverted)} 에서 저정밀 발동이 "
            f"평범한 글보다 **낮다.** §6.2 가 이 8차원을 L1 보조 feature 로 쓰므로 "
            f"그대로 두면 모델이 반대 방향을 배운다."
        )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(
            {
                "high_precision": list(HIGH_PRECISION),
                "expectation": EXPECTATION,
                "fire_threshold": FIRE_THRESHOLD,
                "quiet_threshold": QUIET_THRESHOLD,
                "results": results,
                "all_predictions_matched": all_ok,
                "anti_correlated_techniques": anti_correlated_features(results),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
