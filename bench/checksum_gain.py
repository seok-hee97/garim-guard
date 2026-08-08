"""§7.2 S1 — 체크섬과 생년월일·성별 정합이 오탐을 얼마나 줄이는가.

**이 프로젝트에서 유일하게 사전 등록된 예측이다.** §2.1 이 실측 전에 숫자를 박아뒀고,
빗나갔을 때 무엇을 할지도 적어뒀다.

> 주민등록번호·외국인등록번호는 3중 검증으로 **약 250배**, 사업자·법인·카드는 체크섬만
> 있어 **약 10배**가 예측값이다. **실측이 이 범위에서 크게 벗어나면 코퍼스나 구현을
> 의심한다.**

## 출하 코드를 재는 방법

`marker_probe.py` 가 알고리즘을 제 안에 다시 구현해서 사전 22칸이 비어 있는 것을 못
잡았다. 같은 실수를 반복하지 않으려고 **정규식도 분기도 앵커도 출하본 그대로 두고,
검증 술어만 `True` 로 바꿔 끈다.** `kr_pii` 가 검증 함수를 이름으로 가져오기 때문에
가능하다 — 재구현이 아니라 절개다.

| 팔 | 무엇을 껐나 |
|---|---|
| `regex_only` | 검증 단계 전부 — **출하된 컴파일 패턴 객체**에 직접 물어본다 |
| `checksum` | 생년월일·성별만 (체크섬은 출하본) |
| `full` | 아무것도 안 껐다 = 출하본 |

`regex_only` 만 절개가 아니라 패턴 조회다. `detect_kr_pii` 의 `if gender in "1234"` 가
이름 붙은 함수가 아니라 인라인이라 끌 수가 없고, 끄지 못한 채로 "정규식 단독" 이라고
부르면 분모가 절반으로 줄어 감소 배수를 부풀린다(첫 실행에서 46.2% 로 나왔다 — 8/9 이
아니라 4/9 였다). 그래서 분모는 패턴에서 직접 가져온다.

## 오탐원 — 6자리 앞머리가 날짜냐가 갈림길이다

§2.1 이 고른 네 가지를 실제 형태대로 만든다. 여기서 **미리 말해둘 것**: 주민번호
검증의 셋째 단계는 앞 6자리를 날짜로 읽는다. 그래서 앞머리가 **진짜 날짜인** 주문번호·
사번에는 이 단계가 거의 안 걸리고, **순수 일련번호인** 송장번호에는 크게 걸린다.
250배라는 하나의 숫자가 오탐원에 따라 갈릴 수 있다는 뜻이고, 그게 이 측정의 요점이다.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# §2.1 이 실측 전에 박아둔 예측값. 비교 대상이지 목표가 아니다.
PREDICTED_GAIN = {"rrn_family": 250.0, "checksum_only_family": 10.0}
# "크게 벗어난다" 의 기준을 미리 정한다 — 결과를 보고 정하면 사전 등록이 아니다.
TOLERANCE = 3.0

_ALWAYS_TRUE_IN_KR_PII = {
    "checksum": ("rrn_date_sex_valid",),
    "full": (),
}


@contextmanager
def ablated(arm: str) -> Iterator[None]:
    """검증 술어만 끈다. 정규식·분기·앵커·신뢰도는 출하본 그대로 돈다."""
    from garim.detect.rules import checksum, kr_pii

    names = _ALWAYS_TRUE_IN_KR_PII[arm]
    saved: list[tuple[Any, str, Any]] = []
    for name in names:
        saved.append((kr_pii, name, getattr(kr_pii, name)))
        setattr(kr_pii, name, lambda _value: True)
    if "rrn_date_sex_valid" in names:
        # `rrn_checksum_valid` 는 제 안에서 다시 날짜를 본다(checksum.py:27). 그쪽도
        # 같이 끄지 않으면 "체크섬만" 팔에 날짜 검증이 몰래 남는다.
        saved.append((checksum, "rrn_date_sex_valid", checksum.rrn_date_sex_valid))
        checksum.rrn_date_sex_valid = lambda _value: True
    try:
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    label: str
    code: str
    build: Callable[[random.Random], str]
    family: str
    # 정규식 단독 분모는 **출하된 컴파일 패턴 객체**로 잰다. 여기서 정규식을 다시 쓰면
    # 프로브가 대상과 어긋나 아무것도 못 잡는 그 실패를 되풀이하게 된다.
    pattern: str


def _order_number(rng: random.Random) -> str:
    """주문번호 `YYMMDD-일련번호`. 일련번호는 선행 0 없는 7자리 — 실무 표기다.

    주민번호 정규식은 7번째 자리에 1~8 을 요구하므로 1~9 균등이면 8/9 가 걸린다.
    §2.1 이 적은 88.9% 가 정확히 이 값이다.
    """
    return (
        f"주문번호 {rng.randint(20, 25):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        f"-{rng.randint(1, 9)}{rng.randint(0, 999999):06d} 확인 부탁드립니다"
    )


def _employee_number(rng: random.Random) -> str:
    """사번 — 입사일 6자리 + 부서코드(1~8) + 일련 6자리. 앞머리가 진짜 날짜다."""
    return (
        f"사번 {rng.randint(5, 25):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        f"-{rng.randint(1, 8)}{rng.randint(0, 999999):06d} 입니다"
    )


def _invoice_number(rng: random.Random) -> str:
    """송장번호 — **날짜가 아닌 순수 일련번호** 6자리 + 7자리."""
    return (
        f"송장번호 {rng.randint(100000, 999999)}"
        f"-{rng.randint(1, 8)}{rng.randint(0, 999999):06d} 로 조회하세요"
    )


def _product_serial(rng: random.Random) -> str:
    """제품 시리얼이 사업자등록번호 형식(3-2-5)과 겹친다. 체크섬만 있는 계열이다."""
    return (
        f"제품 시리얼 {rng.randint(100, 999)}-{rng.randint(10, 99):02d}"
        f"-{rng.randint(10000, 99999)} 를 등록했습니다"
    )


SOURCES: tuple[Source, ...] = (
    Source("order", "주문번호", "L-KR-01", _order_number, "rrn_family", "_RRN"),
    Source("employee", "사번(입사일 기반)", "L-KR-01", _employee_number, "rrn_family", "_RRN"),
    Source("invoice", "송장번호", "L-KR-01", _invoice_number, "rrn_family", "_RRN"),
    Source(
        "serial",
        "제품 시리얼(사업자 형식)",
        "L-KR-05",
        _product_serial,
        "checksum_only_family",
        "_BUSINESS",
    ),
)

# ★ 끄지 못한 것이 하나 있다. `detect_kr_pii` 안의 `if gender in "1234"` 는 이름 붙은
#   함수가 아니라 인라인 분기라, 재구현 없이는 못 끈다. 이 분기는 주민/외국인 갈래를
#   나누는 것이라 `checksum`·`full` 두 팔에 그대로 살아 있다. 그래서 `checksum` 팔은
#   엄밀히는 "체크섬 + 성별코드 분류" 다. 감소 배수를 **과소평가**하는 방향이라
#   예측을 유리하게 만들지는 않는다.
RESIDUAL_GATE = 'detect_kr_pii 의 인라인 `gender in "1234"` 는 끄지 못했다 (재구현 없이 불가)'


def measure(source: Source, *, count: int, seed: int) -> dict[str, float]:
    """한 오탐원을 세 팔에 태운다. 같은 문자열을 쓰므로 팔 사이 차이는 검증뿐이다."""
    from garim.detect.rules import kr_pii

    rng = random.Random(seed)
    corpus = [source.build(rng) for _ in range(count)]
    pattern = getattr(kr_pii, source.pattern)
    rates = {"regex_only": sum(bool(pattern.search(text)) for text in corpus) / count}
    for arm in ("checksum", "full"):
        with ablated(arm):
            hits = sum(
                any(f.code == source.code for f in kr_pii.detect_kr_pii(text)) for text in corpus
            )
        rates[arm] = hits / count
    return rates


def _gain(rates: dict[str, float]) -> float | None:
    """정규식 단독 대비 몇 배 줄었나. 0 이 되면 배수가 정의되지 않는다."""
    if not rates["full"]:
        return None
    return rates["regex_only"] / rates["full"]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="§7.2 S1 체크섬 기여도")
    parser.add_argument("--count", type=int, default=20_000, help="§2.1 은 오탐원당 2만 건")
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--output", default="artifacts/checksum_gain.json")
    args = parser.parse_args()

    results: dict[str, Any] = {}
    print(f"오탐원당 n={args.count:,}\n")
    header = f"{'오탐원':<24}{'정규식 단독':>11}{'+체크섬':>10}{'+날짜·성별':>11}{'감소 배수':>11}"
    print(header)
    for index, source in enumerate(SOURCES):
        rates = measure(source, count=args.count, seed=args.seed + index)
        gain = _gain(rates)
        results[source.key] = {
            "label": source.label,
            "code": source.code,
            "family": source.family,
            **{arm: round(value, 5) for arm, value in rates.items()},
            "gain": None if gain is None else round(gain, 1),
            "predicted_gain": PREDICTED_GAIN[source.family],
        }
        shown = "∞" if gain is None else f"{gain:.1f}×"
        print(
            f"{source.label:<24}{rates['regex_only']:>10.1%}{rates['checksum']:>10.1%}"
            f"{rates['full']:>10.1%}{shown:>11}"
        )

    print(f"\n⚠️ {RESIDUAL_GATE}")
    print("\n§2.1 예측 대조 — 예측은 실측 전에 박은 것이고 목표가 아니다")
    verdicts: dict[str, str] = {}
    for key, row in results.items():
        predicted, gain = row["predicted_gain"], row["gain"]
        if gain is None:
            verdict = "판정 불가 — 출하본 오탐이 0 이라 배수가 정의되지 않는다"
        elif 1 / TOLERANCE <= gain / predicted <= TOLERANCE:
            verdict = f"예측 {predicted:.0f}× 대비 {gain / predicted:.2f}배 — 범위 안"
        else:
            verdict = (
                f"★ 예측 {predicted:.0f}× 대비 {gain / predicted:.2f}배 — "
                f"{TOLERANCE:.0f}배 허용을 벗어났다"
            )
        verdicts[key] = verdict
        print(f"  {results[key]['label']:<24} {verdict}")

    payload = {
        "count_per_source": args.count,
        "tolerance": TOLERANCE,
        "residual_gate": RESIDUAL_GATE,
        "sources": results,
        "verdicts": verdicts,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
