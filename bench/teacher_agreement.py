"""교사 라벨이 무엇에 동의하는가 — 두 가지를 잰다.

## ① 생성기 라벨과의 일치도

`make_teacher_input.py` 가 생성기의 4-way 라벨을 벗겨 사이드카에 보관한다. 교사가 붙인
라벨과 대조하면 **세 독립 모델이 우리 생성기의 라벨에 동의하는지**가 나온다. 자작
코퍼스를 외부 모델로 검증할 유일한 기회이고, 정답을 이미 갖고 있어 추가 비용이 0이다.
일치도가 낮으면 공격 코퍼스 전체를 의심해야 한다(§7.6-3).

## ② JAILBREAK 동의율 — 교사 교체가 검증 효과를 남겼는가

§6.2.2 는 JAILBREAK 를 **Gemma 단독으로 두지 않으려고** 세 번째 교사를 넣었다.

    minimum_votes: 2
    gemma4:  votes_for: [... JAILBREAK]
    qwen:    votes_for: [JAILBREAK]      ← 이 자리

Stream 이 우리 pin 에서 안 돌아 Gen-0.6B 로 갈아탔는데, §6.2.2 가 **Gen-0.6B 의 폴란드
FPR 17.17%** 를 명시해 뒀다. 과탐이 심한 교사가 2표 중 1표를 맡으면 **거의 항상 동의해서
사실상 Gemma 단독 판정**이 된다 — 설계가 막으려던 바로 그 상태다.

그래서 재는 것은 정확도가 아니라 **독립성**이다.

    Gemma 가 JAILBREAK 이라 한 행에서 Qwen 이 동의한 비율
      너무 높으면(≥ 0.95) 검증 효과가 없다 — 교체 실패로 본다
    Gemma 가 JAILBREAK 이 **아니라** 한 행에서 Qwen 이 JAILBREAK 이라 한 비율
      이건 `can_flip_none: false` 가 막지만, 크면 교사가 과탐한다는 신호다

⚠️ 이 하네스는 판정을 내리지 않는다. 숫자를 내고 임계를 함께 적어 둘 뿐이다 — 어느
쪽으로 갈지는 사람이 정한다.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from garim.training.teacher_inference import UNPARSED_SUFFIX
from garim.training.teacher_io import TeacherArtifact, load_teacher_artifacts
from garim.training.teachers import load_teacher_policy

# 이 위를 넘으면 두 번째 표가 첫 번째 표를 그대로 따라 하는 셈이라 검증이 아니다.
RUBBER_STAMP_THRESHOLD = 0.95
# `make_teacher_input.py` 가 분할별로 남기는 사이드카. 25a3528 에서 한 파일이 둘로 갈렸다.
DEFAULT_GOLD = (
    "data/corpus/attack-generator-gold-train.json",
    "data/corpus/attack-generator-gold-validation.json",
)


def load_artifacts(path: str | Path) -> dict[str, TeacherArtifact]:
    """Compatibility wrapper using the strict loader (including duplicate-ID checks)."""

    return load_teacher_artifacts(path)


def generator_agreement(
    artifacts: dict[str, TeacherArtifact],
    gold: dict[str, str],
    *,
    eligible_labels: set[str],
) -> dict[str, Any]:
    """교사 라벨 vs 생성기 라벨. 교사가 투표하지 않는 클래스는 셈에서 뺀다."""
    shared = sorted(
        key for key in set(artifacts) & set(gold) if gold[key] in eligible_labels
    )
    if not shared:
        raise ValueError(
            "no eligible overlapping ids between artifacts and the generator sidecar"
        )
    matches = sum(artifacts[key].label == gold[key] for key in shared)
    confusion: Counter[tuple[str, str]] = Counter(
        (gold[key], artifacts[key].label) for key in shared
    )
    return {
        "rows": len(shared),
        "agreement": round(matches / len(shared), 4),
        "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items())},
    }


def jailbreak_independence(
    primary: dict[str, TeacherArtifact], secondary: dict[str, TeacherArtifact]
) -> dict[str, Any]:
    """`secondary` 가 `primary` 의 JAILBREAK 판정을 그대로 따라 하는가."""
    shared = sorted(set(primary) & set(secondary))
    positive = [key for key in shared if primary[key].label == "JAILBREAK"]
    negative = [key for key in shared if primary[key].label != "JAILBREAK"]
    agree = sum(secondary[key].label == "JAILBREAK" for key in positive)
    flip = sum(secondary[key].label == "JAILBREAK" for key in negative)
    rate = agree / len(positive) if positive else float("nan")
    return {
        "primary_jailbreak_rows": len(positive),
        "agreement_on_jailbreak": None if rate != rate else round(rate, 4),
        "rubber_stamp_threshold": RUBBER_STAMP_THRESHOLD,
        # 임계를 넘으면 두 번째 표가 정보를 더하지 않는다 — 교체 실패 신호다.
        "is_rubber_stamp": bool(positive) and rate >= RUBBER_STAMP_THRESHOLD,
        "jailbreak_on_primary_negative": len(negative) and round(flip / len(negative), 4),
        "note": (
            "can_flip_none=false 가 마지막 항목의 피해를 막지만, 크면 교사가 과탐한다"
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="교사 일치도 · JAILBREAK 독립성")
    parser.add_argument(
        "--gold",
        action="append",
        help="생성기 라벨 사이드카 JSON. 여러 번 줄 수 있다. 생략하면 분할별 두 파일",
    )
    parser.add_argument("--teacher", action="append", required=True, help="name=path.jsonl")
    parser.add_argument("--primary", default="gemma4")
    parser.add_argument("--secondary", default="qwen3guard_gen")
    parser.add_argument("--policy", default="train/teachers.yaml")
    parser.add_argument("--output", default="artifacts/teacher_agreement.json")
    args = parser.parse_args(argv)

    loaded = {}
    for entry in args.teacher:
        name, _, path = entry.partition("=")
        if not path:
            raise SystemExit(f"--teacher expects name=path, got {entry!r}")
        if name in loaded:
            raise SystemExit(f"duplicate --teacher name: {name}")
        loaded[name] = load_artifacts(path)

    gold: dict[str, str] = {}
    for path in args.gold or DEFAULT_GOLD:
        part = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(part, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in part.items()
        ):
            raise SystemExit(f"{path}: gold must be a JSON object mapping string IDs to labels")
        conflicts = [key for key in set(part) & set(gold) if part[key] != gold[key]]
        if conflicts:
            raise SystemExit(f"{path}: {len(conflicts)} IDs carry a different gold label")
        gold.update(part)
    policy = load_teacher_policy(args.policy)
    unknown = set(loaded) - set(policy.teachers)
    if unknown:
        raise SystemExit(f"teachers not defined by {args.policy}: {sorted(unknown)}")
    # ★ 교사 풀은 생성기 행보다 크다 — PolyGuard·deepset 은 생성기 라벨이 없다. 초판은
    #   교사 산출물이 gold 와 **정확히** 같은 ID 를 덮어야 한다고 요구해서, 풀이 커진 뒤
    #   (25a3528) 6b 단계가 GPU 교사 실행 직후 `extra=17,787` 로 멈추게 돼 있었다.
    #   요구할 것은 둘이다 — 교사끼리 같은 행을 봤는가, 생성기 행을 하나도 빠뜨리지 않았는가.
    gold_ids = set(gold)
    coverage = {name: set(rows) for name, rows in loaded.items()}
    pool_ids = next(iter(coverage.values()))
    if any(ids != pool_ids for ids in coverage.values()):
        sizes = {name: len(ids) for name, ids in coverage.items()}
        raise SystemExit(f"teachers cover different ID sets {sizes} — 같은 입력 파일로 돌렸는가")
    missing_gold = gold_ids - pool_ids
    if missing_gold:
        raise SystemExit(f"teacher artifacts miss {len(missing_gold)} generator-labeled rows")
    for name, rows in loaded.items():
        spec = policy.teachers[name]
        drifted = [
            artifact.id
            for artifact in rows.values()
            if artifact.teacher != name
            or artifact.model_id != spec.model_id
            or artifact.revision != spec.revision
        ]
        if drifted:
            raise SystemExit(
                f"{name}: {len(drifted)} artifacts have teacher/model/revision drift"
            )
    # 읽지 못해 기권한 행(신뢰도 0, `:unparsed`)은 표를 던지지 않았다. 일치도·독립성에 넣으면
    # 그 행의 자리표시 라벨이 숫자를 흐린다 — 빼고 세고, 몇 행인지 따로 적는다.
    abstained = {
        name: {
            key
            for key, artifact in rows.items()
            if (artifact.prompt_version or "").endswith(UNPARSED_SUFFIX)
        }
        for name, rows in loaded.items()
    }
    voting = {
        name: {key: artifact for key, artifact in rows.items() if key not in abstained[name]}
        for name, rows in loaded.items()
    }
    payload: dict[str, Any] = {
        "pool_rows": len(pool_ids),
        "generator_rows": len(gold_ids),
        "abstained": {name: len(keys) for name, keys in sorted(abstained.items())},
        "generator_agreement": {
            name: generator_agreement(
                rows, gold, eligible_labels=set(policy.teachers[name].votes_for)
            )
            for name, rows in sorted(voting.items())
        }
    }
    if args.primary in loaded and args.secondary in loaded:
        payload["jailbreak_independence"] = jailbreak_independence(
            voting[args.primary], voting[args.secondary]
        )

    for name, report in payload["generator_agreement"].items():
        print(f"  {name:20} n={report['rows']:<6} 생성기 라벨과 일치 {report['agreement']:.3f}")
    independence = payload.get("jailbreak_independence")
    if independence:
        rate = independence["agreement_on_jailbreak"]
        shown = "n/a" if rate is None else f"{rate:.3f}"
        print(
            f"\n  JAILBREAK 동의율 {shown} "
            f"(n={independence['primary_jailbreak_rows']}, "
            f"고무도장 임계 {RUBBER_STAMP_THRESHOLD})"
        )
        if independence["is_rubber_stamp"]:
            print("  ★ 두 번째 표가 첫 번째를 그대로 따라 한다 — 검증 효과가 없다")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"-> {args.output}")
    if independence and (
        independence["agreement_on_jailbreak"] is None
        or independence["is_rubber_stamp"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
