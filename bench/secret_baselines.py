"""§7.3 베이스라인 — 기성 시크릿 스캐너를 가림과 같은 자극에 태운다.

§2.4 가 CredData 기준 수치를 인용해 도구를 골랐다 — CredSweeper F1 0.859,
detect-secrets 0.207. 그런데 그 수치는 **영어 코드 저장소** 기준이다. §9-2 가 주장하는
것은 *"한국어 프롬프트에서 기성 시스템이 얼마나 무너지는가"* 이고, 그건 우리 자극에
태워 봐야 안다.

여기서 재는 것은 축 (d) 슬라이스다 — 같은 문장 골격에 진짜 시크릿과 플레이스홀더를
넣어 **값으로만 갈리게** 만든 셋이다(`training/secret_slice.py`).

⚠️ 이 하네스는 도구가 설치돼 있을 때만 돈다. 없으면 그 판정자를 조용히 건너뛰지 않고
목록에서 빼고 그 사실을 보고한다 — "안 돌았다" 와 "0점" 은 다르다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Detector = Callable[[str], bool]


@dataclass(slots=True)
class BaselineScore:
    name: str
    available: bool
    positives: int = 0
    negatives: int = 0
    recall: float = 0.0
    false_positive_rate: float = 0.0
    note: str = ""

    @property
    def f1(self) -> float:
        precision_denominator = self.recall * self.positives + (
            self.false_positive_rate * self.negatives
        )
        if not precision_denominator:
            return 0.0
        precision = (self.recall * self.positives) / precision_denominator
        total = precision + self.recall
        return 2 * precision * self.recall / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        if not self.available:
            return {"name": self.name, "available": False, "note": self.note}
        return {
            "name": self.name,
            "available": True,
            "recall": round(self.recall, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "f1": round(self.f1, 4),
            "positives": self.positives,
            "negatives": self.negatives,
        }


def garim_secrets(policy_path: str = "config/policy.yaml") -> Detector:
    """★ **정책 판정**을 쓴다 — 원시 규칙 배출이 아니다.

    비교 대상 도구들은 "이걸 시크릿이라고 신고하는가" 를 낸다. 가림의 대응물은
    사용자가 실제로 보는 판정이지, 강등된 채 감사에만 남는 Finding 이 아니다.
    원시 배출로 재면 플레이스홀더 강등(§2.4 (C))이 통째로 안 보인다 — 실제로
    그렇게 재다가 0.170 이 안 내려간 것처럼 보였다.
    """
    from garim import Inspector

    inspector = Inspector(policy_path)
    return lambda text: bool(inspector.check(text).leaks)


def garim_raw_rules() -> Detector:
    """정책 이전의 원시 규칙 배출. 강등이 판정에 미치는 효과를 분리해 본다."""
    from garim.detect.rules import detect_secrets

    return lambda text: bool(detect_secrets(text))


def detect_secrets_baseline() -> Detector | None:
    """`detect-secrets` — §2.4 표의 precision 0.1416 도구. LLM Guard 가 쓰는 것."""
    try:
        from detect_secrets.core import plugins
        from detect_secrets.settings import default_settings
    except ImportError:
        return None

    def detector(text: str) -> bool:
        with default_settings() as settings:
            loaded = [plugins.initialize.from_plugin_classname(name) for name in settings.plugins]
            for line_number, line in enumerate(text.splitlines(), start=1):
                for plugin in loaded:
                    try:
                        found = plugin.analyze_line(
                            filename="input.txt", line=line, line_number=line_number
                        )
                        if found:
                            return True
                    except Exception:  # 플러그인별 예외를 전체 실패로 만들지 않는다
                        continue
        return False

    return detector


def credsweeper_baseline() -> Detector | None:
    """`CredSweeper` — §2.4 가 (A)(B) 기반으로 채택한다고 적은 도구(Samsung, MIT)."""
    try:
        from credsweeper.app import CredSweeper
        from credsweeper.file_handler.string_content_provider import (
            StringContentProvider,
        )
    except ImportError:
        return None

    engine = CredSweeper()

    def detector(text: str) -> bool:
        provider = StringContentProvider(text.splitlines())
        try:
            return bool(engine.file_scan(provider))
        except Exception:
            return False

    return detector


def evaluate(
    positives: Sequence[str], negatives: Sequence[str], detector: Detector, *, name: str
) -> BaselineScore:
    if not positives or not negatives:
        raise ValueError("baseline evaluation needs both positives and negatives")
    hit_positive = sum(detector(text) for text in positives)
    hit_negative = sum(detector(text) for text in negatives)
    return BaselineScore(
        name=name,
        available=True,
        positives=len(positives),
        negatives=len(negatives),
        recall=hit_positive / len(positives),
        false_positive_rate=hit_negative / len(negatives),
    )


def run(positives: Sequence[str], negatives: Sequence[str]) -> list[BaselineScore]:
    candidates: list[tuple[str, Detector | None]] = [
        ("garim-l0", garim_secrets()),
        ("garim-raw-rules", garim_raw_rules()),
        ("detect-secrets", detect_secrets_baseline()),
        ("credsweeper", credsweeper_baseline()),
    ]
    scores: list[BaselineScore] = []
    for name, detector in candidates:
        if detector is None:
            # 설치 안 됐다고 0점을 주지 않는다 — "안 돌았다" 와 "못 잡았다" 는 다르다.
            scores.append(BaselineScore(name=name, available=False, note="not installed; skipped"))
            continue
        scores.append(evaluate(positives, negatives, detector, name=name))
    return scores


def main() -> int:
    import argparse

    from garim.training.secret_slice import generate_secret_slice

    parser = argparse.ArgumentParser(description="§7.3 시크릿 베이스라인 (축 d)")
    parser.add_argument("--output")
    args = parser.parse_args()

    rows = generate_secret_slice()
    positives = [r.text for r in rows if r.meta["expected_secret"]]
    negatives = [r.text for r in rows if not r.meta["expected_secret"]]
    scores = run(positives, negatives)

    print(f"축 (d) 슬라이스 — 진짜 {len(positives)} / 플레이스홀더 {len(negatives)}")
    print(f"{'도구':18} {'재현율':>8} {'오탐율':>8} {'F1':>8}")
    for score in scores:
        if not score.available:
            print(f"{score.name:18} {'—':>8} {'—':>8} {'—':>8}  ({score.note})")
            continue
        print(
            f"{score.name:18} {score.recall:>8.3f} {score.false_positive_rate:>8.3f} "
            f"{score.f1:>8.3f}"
        )
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(
                {"slice": "axis-d secret", "scores": [s.as_dict() for s in scores]},
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
