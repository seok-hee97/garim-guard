"""§7.3 베이스라인 — Presidio 두 팔. H1 의 유일한 외부 대조다.

§7.3 이 이 항목에 붙인 경고를 그대로 옮긴다.

> 초안은 "한국 고유 식별자 recognizer 가 없다" 고 적었다 — **거짓이다.**
> `KrRrnRecognizer`·`KrFrnRecognizer`·`KrBrnRecognizer`·`KrPassportRecognizer`·
> `KrDriverLicenseRecognizer` 5종이 MIT 로 배포된다. **반드시 활성 상태로 돌린다** —
> 끄고 비교하면 H1 의 유일한 외부 대조가 0점짜리 허수아비가 된다.

그래서 팔을 둘로 나눈다.

| 팔 | 무엇 | §7.3 의 위치 |
|---|---|---|
| `presidio_ko` | Kr* 5종 **활성** + 공용 패턴 | 진짜 대조 상대 |
| `presidio_vanilla` | 영어 기본값, Kr* **없음** | 참고용 하한. **이것만 보고하면 안 된다** |

## spaCy 를 안 쓴다

`AnalyzerEngine` 은 NLP 엔진을 요구하지만 여기서 비교하는 것은 **패턴 recognizer** 다
(주민·외국인·사업자·여권·운전면허·이메일·전화·카드). NER 이 필요한 `PERSON`·`LOCATION`
은 한국어 spaCy 모델이 없으면 어차피 못 잡고, 영어 모델을 한국어에 태우면 하한도 상한도
아닌 잡음이 된다. 그래서 recognizer 를 직접 호출하고 **`PERSON`·`ADDRESS` 는 두 팔 모두
빈칸으로 둔다** — 못 잡는 것을 0.000 으로 적는 것이지, 안 재고 비운 것이 아니다.

## Presidio 가 우리보다 안전했던 지점 (§2.1)

`KrRrnRecognizer` 는 2020-10 이후 발급분에 대해 `False` 가 아니라 **`None` 을 반환**한다
(소스 주석: *"This validation is only for RRNs issued before October 2020. Therefore,
it returns None, not False"*). 초안의 "체크섬 실패 → 탐지 아님" 은 무료 오픈소스
베이스라인보다 **위험한 방향**이었다. 이 팔을 돌리는 것은 그 교정이 실제로 반영됐는지
확인하는 일이기도 하다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Detector = Callable[[str], list[tuple[int, int, str]]]

# Presidio 엔티티 → 가림 라벨. 없는 쪽은 안 적는다 — 억지로 이으면 비교가 거짓이 된다.
_ENTITY_TO_LABEL: dict[str, str] = {
    "KR_RRN": "KR_RRN",
    "KR_FRN": "KR_FRN",
    "KR_PASSPORT": "KR_PASSPORT",
    "KR_DRIVER_LICENSE": "KR_DL",
    "KR_BRN": "KR_BIZNO",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CARD",
    "IP_ADDRESS": "NET_ADDR",
    "URL": "NET_ADDR",
}
# Presidio 에 대응 recognizer 가 아예 없는 라벨. §7.3 의 "남는 갭" 이 이것이다.
UNCOVERED_LABELS = ("KR_CORPNO", "KR_NHI", "KR_ACCOUNT", "PERSON", "ADDRESS", "ORG_ID")

_KOREAN_RECOGNIZERS = (
    "KrRrnRecognizer",
    "KrFrnRecognizer",
    "KrBrnRecognizer",
    "KrPassportRecognizer",
    "KrDriverLicenseRecognizer",
)
_SHARED_RECOGNIZERS = (
    "EmailRecognizer",
    "CreditCardRecognizer",
    "IpRecognizer",
    "UrlRecognizer",
)

# ★ 이 칸은 비교가 성립하지 않는다 — 재지 말고 빼야 한다.
#
# Presidio 의 `EmailRecognizer` 는 tldextract 로 **실제 TLD 인지 검증**한다. 우리 합성
# 코퍼스는 `@example.test` 를 쓰는데 `.test` 는 예약 TLD라 검증에서 떨어진다. 그래서
# Presidio 의 EMAIL F1 이 0.000 으로 나오는데, `@example.com` 을 주면 신뢰도 1.0 으로
# 정확히 잡는다. 이걸 우리 승리로 적으면 §7.3 이 경고한 **바로 그 허수아비**다.
#
# 코퍼스를 실제 도메인으로 바꾸는 선택지는 없다 — 예약 TLD 를 쓰는 이유가 합성 값이
# 실존 주소를 때리지 않게 하려는 것이다. 그래서 칸을 비우고 이유를 적는다.
INVALID_COMPARISON_LABELS = {
    "EMAIL": "합성 코퍼스가 예약 TLD(.test)를 쓴다 — Presidio 의 TLD 검증이 떨어뜨린다",
}


def _load(names: Sequence[str]) -> list[Any]:
    import tldextract
    import tldextract.tldextract as tldextract_module
    from presidio_analyzer import predefined_recognizers
    from presidio_analyzer.predefined_recognizers import PhoneRecognizer

    # Presidio's EmailRecognizer uses the module-level extractor, whose default tries a
    # writable user cache and may refresh the suffix list over the network.  A pinned
    # benchmark must do neither: use tldextract's packaged snapshot deterministically.
    extractor = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())
    # ``extract`` closes over the implementation module's global, while callers may
    # also inspect the package-level export. Keep both references deterministic.
    tldextract.TLD_EXTRACTOR = extractor
    tldextract_module.TLD_EXTRACTOR = extractor
    loaded = [getattr(predefined_recognizers, name)() for name in names]
    # 전화번호는 지역을 줘야 한다. 기본값(US)으로 한국 번호를 재면 Presidio 가 아니라
    # 잘못 설정된 Presidio 를 재게 된다.
    loaded.append(PhoneRecognizer(supported_regions=["KR"]))
    return loaded


def _detector(recognizers: Sequence[Any], *, score_threshold: float) -> Detector:
    def detector(text: str) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for recognizer in recognizers:
            for result in recognizer.analyze(
                text, entities=recognizer.supported_entities, nlp_artifacts=None
            ):
                label = _ENTITY_TO_LABEL.get(result.entity_type)
                if label is None or result.score < score_threshold:
                    continue
                spans.append((result.start, result.end, label))
        return spans

    return detector


def presidio_ko_detector(*, score_threshold: float = 0.0) -> Detector:
    """§7.3 이 "반드시 활성 상태로" 라고 못 박은 팔.

    임계값 기본이 0.0 인 이유 — Presidio 의 사업자번호·운전면허 패턴은 점수가 0.05~0.1 로
    아주 낮다. 임계를 올리면 그 5종 중 셋이 조용히 빠져 §7.3 이 경고한 **허수아비**가
    된다. 낮은 점수는 Presidio 자신의 검증 함수가 걸러내도록 두는 것이 설계 의도다.
    """
    return _detector(
        _load([*_KOREAN_RECOGNIZERS, *_SHARED_RECOGNIZERS]), score_threshold=score_threshold
    )


def presidio_vanilla_detector(*, score_threshold: float = 0.0) -> Detector:
    """영어 기본 설정 — 한국 recognizer 없음. **참고용 하한이다.**"""
    return _detector(_load(_SHARED_RECOGNIZERS), score_threshold=score_threshold)


# §7.3 은 Kr* 를 끄고 비교하는 것을 허수아비라고 경고했다. 그 **거울상**도 똑같이
# 부정직하다 — 아무도 배포하지 않을 0.05 점 패턴을 켠 채로 오탐을 세면 Presidio 를
# 일부러 나쁘게 만드는 것이다. 그래서 실제 배포에서 흔한 임계값을 한 팔 더 둔다.
STRICT_THRESHOLD = 0.4


def main() -> int:
    import argparse

    from bench.leak_eval import evaluate, garim_leak_detector, write_report
    from garim.training.synthetic import generate_pii_corpus

    parser = argparse.ArgumentParser(description="§7.3 Presidio 베이스라인 2종")
    parser.add_argument("--split", default="test")
    parser.add_argument("--output", default="artifacts/presidio_baseline.json")
    args = parser.parse_args()

    records = [r for r in generate_pii_corpus() if r.split == args.split]
    if not records:
        print(f"split={args.split} 에 레코드가 없다")
        return 2

    arms: list[tuple[str, Detector]] = [
        ("garim_l0", garim_leak_detector()),
        ("presidio_ko", presidio_ko_detector()),
        ("presidio_ko_strict", presidio_ko_detector(score_threshold=STRICT_THRESHOLD)),
        ("presidio_vanilla", presidio_vanilla_detector()),
    ]
    reports = [evaluate(records, detector, name=name) for name, detector in arms]

    # L1 전담 라벨(PERSON/ADDRESS)은 가중치가 없어 세 팔 모두 0.000 이다. macro 에
    # 섞으면 "누가 더 나은가" 가 아니라 "0 을 몇 칸 들고 있나" 를 재게 된다.
    l1_only = {"PERSON", "ADDRESS"}
    excluded = l1_only | set(INVALID_COMPARISON_LABELS)
    print(f"split={args.split} · n={len(records)}\n")
    print(f"{'팔':<20} {'macro F1':>9} {'비교가능 macro':>14} {'세그먼트 FPR':>13}")
    for report in reports:
        payload = report.as_dict()
        measurable = [
            score["f1"] for label, score in payload["by_label"].items() if label not in excluded
        ]
        print(
            f"{payload['detector']:<20} {payload['macro_f1']:>9.3f} "
            f"{sum(measurable) / len(measurable):>14.3f} "
            f"{payload['segment_false_positive_rate']:>13.3f}"
        )
    print(f"\n⚠️ L1 가중치가 없어 세 팔 모두 0.000 인 라벨: {', '.join(sorted(l1_only))}")
    print(f"⚠️ Presidio 에 대응 recognizer 가 없는 라벨: {', '.join(UNCOVERED_LABELS)}")
    for label, reason in INVALID_COMPARISON_LABELS.items():
        print(f"⚠️ {label} 은 비교 불성립이라 macro 에서 뺐다 — {reason}")
    print("⚠️ vanilla 는 참고용 하한이다 — 이것만 보고하면 안 된다(§7.3).")

    write_report(args.output, reports)
    # 라벨별 대조는 macro 하나로 안 보인다 — 어디서 지고 어디서 이기는지가 §7.3 의 요점이다.
    per_label = {
        report.as_dict()["detector"]: {
            label: round(score["f1"], 4) for label, score in report.as_dict()["by_label"].items()
        }
        for report in reports
    }
    Path(args.output).with_suffix(".by_label.json").write_text(
        json.dumps(per_label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
